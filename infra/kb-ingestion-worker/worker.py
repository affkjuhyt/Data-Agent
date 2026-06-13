"""
Kafka consume loop — handles silver and gold ingestion stages.
All imports are local (worker dir) or shared libraries (services/, basemodel/).
No imports from app/.
"""
from __future__ import annotations

import asyncio
import logging

from services.database_connector.kafka_connector import KafkaConsumerClient, KafkaProducerClient
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import QdrantClient as QdrantService
from services.database_connector.neo4j_connector import Neo4jClient

import config as cfg
from silver import promote_to_silver
from gold import promote_to_gold
from docling_parser import InlineDoclingParser
from log_writer import write_log
from pipeline_events import PipelineEventEmitter
from conflict_resolver import fetch_awaiting_conflict, _resolve_conflict

log = logging.getLogger(__name__)


async def consume_loop(
    consumer: KafkaConsumerClient,
    kafka_producer: KafkaProducerClient,
    minio: MinIOClient,
    postgres: PostgresClient,
    mongo: MongoClient,
    qdrant_svc: QdrantService,
    neo4j_svc: Neo4jClient,
    model: ModelServiceClient,
    docling_parser: InlineDoclingParser,
) -> None:
    db = mongo.get_client()

    # Per-stage concurrency caps. The consume loop may dispatch up to
    # CONSUME_CONCURRENCY handlers at once; these semaphores then bound how many
    # of each stage actually run in parallel. Silver (CPU/RAM-heavy Docling OCR)
    # is kept low to stay under the worker memory limit; gold (I/O-bound) is high.
    silver_sem   = asyncio.Semaphore(cfg.SILVER_CONCURRENCY)
    gold_sem     = asyncio.Semaphore(cfg.GOLD_CONCURRENCY)
    conflict_sem = asyncio.Semaphore(cfg.CONFLICT_CONCURRENCY)

    async def handle_silver(data_id: str, tenant_id: str, approved_by: str) -> None:
        async with silver_sem:
            # Reset log document on each new attempt so stale failure entries
            # from previous runs don't replay through the /progress SSE.
            try:
                await db["kb_ingestion_logs"].replace_one(
                    {"data_id": data_id},
                    {"data_id": data_id, "status": "RUNNING", "stage": "silver",
                     "logs": [], "updated_at": __import__("datetime").datetime.utcnow()},
                    upsert=True,
                )
            except Exception:
                pass
            await write_log(db, data_id, "RUNNING", "Silver stage started")
            emitter = await PipelineEventEmitter.create(db, data_id)
            try:
                await write_log(db, data_id, "CHUNKING", "Parsing document with Docling")
                result = await promote_to_silver(
                    minio=minio,
                    postgres=postgres,
                    docling=docling_parser,
                    mongo=mongo,
                    kafka_producer=kafka_producer,
                    data_id=data_id,
                    tenant_id=tenant_id,
                    approved_by=approved_by,
                    emitter=emitter,
                )
                await write_log(
                    db, data_id, "EMBEDDING",
                    f"Silver complete — {result['chunks_count']} chunks accepted, queued for gold",
                )
            except Exception as exc:
                log.error("Worker: Silver failed data_id=%s: %s", data_id, exc)
                await write_log(db, data_id, "FAILED", f"Silver stage failed: {exc}", level="ERROR")
                try:
                    await emitter.emit("pipeline.error", {"stage": "silver", "error": str(exc)[:300]})
                except Exception:
                    pass

    async def handle_gold(data_id: str, tenant_id: str, approved_by: str) -> None:
        async with gold_sem:
            try:
                await db["kb_ingestion_logs"].replace_one(
                    {"data_id": data_id},
                    {"data_id": data_id, "status": "GRAPH_EXTRACTING", "stage": "gold",
                     "logs": [], "updated_at": __import__("datetime").datetime.utcnow()},
                    upsert=True,
                )
            except Exception:
                pass
            await write_log(db, data_id, "GRAPH_EXTRACTING", "Gold stage started — extracting entities")
            emitter = await PipelineEventEmitter.create(db, data_id)
            try:
                result = await promote_to_gold(
                    postgres=postgres,
                    qdrant=qdrant_svc.get_client(),
                    neo4j=neo4j_svc.get_client(),
                    mongo=mongo,
                    model=model,
                    data_id=data_id,
                    tenant_id=tenant_id,
                    approved_by=approved_by,
                    emitter=emitter,
                )
                msg = (
                    f"Gold complete — {result['committed']} blocks committed, "
                    f"{result['conflicted']} conflicts detected"
                )
                await write_log(db, data_id, "PUBLISHED", msg)
            except Exception as exc:
                log.error("Worker: Gold failed data_id=%s: %s", data_id, exc)
                await write_log(db, data_id, "FAILED", f"Gold stage failed: {exc}", level="ERROR")
                try:
                    await emitter.emit("pipeline.error", {"stage": "gold", "error": str(exc)[:300]})
                except Exception:
                    pass

    async def handle_conflict_resolve(conflict_id: str) -> None:
        async with conflict_sem:
            conflict = await fetch_awaiting_conflict(postgres, conflict_id)
            if conflict is None:
                log.warning(
                    "Worker: conflict_id=%s not found or already resolved — skipping",
                    conflict_id,
                )
                return
            await _resolve_conflict(conflict, postgres, qdrant_svc, neo4j_svc, model)

    async def handler(event: dict) -> None:
        stage       = event.get("event", "")
        data_id     = event.get("data_id", "")
        tenant_id   = event.get("tenant_id", "")
        approved_by = event.get("approved_by", "system")

        if stage == "promote_silver":
            if not data_id or not tenant_id:
                log.warning("Worker: promote_silver event missing required fields: %s", event)
                return
            await handle_silver(data_id, tenant_id, approved_by)
        elif stage == "promote_gold":
            if not data_id or not tenant_id:
                log.warning("Worker: promote_gold event missing required fields: %s", event)
                return
            await handle_gold(data_id, tenant_id, approved_by)
        elif stage == "resolve_conflict":
            conflict_id = event.get("conflict_id", "")
            if not conflict_id:
                log.warning("Worker: resolve_conflict event missing conflict_id: %s", event)
                return
            await handle_conflict_resolve(conflict_id)
        else:
            log.warning("Worker: unknown event type %r", stage)

    await consumer.start_consuming(
        topics=[cfg.KAFKA_TOPIC_SILVER, cfg.KAFKA_TOPIC_GOLD, cfg.KAFKA_TOPIC_CONFLICT_RESOLVE],
        handler=handler,
        concurrency=cfg.CONSUME_CONCURRENCY,
    )
