"""
Kafka consume loop — handles silver and gold ingestion stages.
All imports are local (worker dir) or shared libraries (services/, basemodel/).
No imports from app/.
"""
from __future__ import annotations

import asyncio

from services.database_connector.kafka_connector import KafkaConsumerClient, KafkaProducerClient
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import QdrantClient as QdrantService

from utils import config as cfg
from utils.docling_parser import InlineDoclingParser
from utils.graph_store import GraphStore
from utils.pipeline_events import PipelineEventEmitter
from silver import promote_to_silver
from gold import promote_to_gold
from conflict_resolver import resolve_batch

_TERMINAL_EVENTS = ("pipeline.done", "pipeline.error")


async def _route_ingestion_failure(
        producer: KafkaProducerClient | None,
        *,
        stage: str,
        source_topic: str,
        event: dict,
        exc: Exception,
) -> None:
    """Retry-or-dead-letter a failed silver/gold document instead of dropping it."""
    attempts = int(event.get("attempts", 0)) + 1
    payload = {**event, "attempts": attempts}
    data_id = event.get("data_id", "")
    if producer is None:
        raise RuntimeError(
            f"no producer configured — cannot DLQ/retry {stage} data_id={data_id} (attempt {attempts})"
        ) from exc
    if attempts < cfg.INGESTION_MAX_ATTEMPTS:
        await producer.produce(source_topic, value=payload, key=data_id or None)
        return
    dlq_payload = {
        **payload,
        "failed_stage": stage,
        "error": str(exc)[:500],
        "source_topic": source_topic,
    }
    await producer.produce(cfg.KAFKA_TOPIC_DLQ, value=dlq_payload, key=data_id or None)


async def _reset_events_for_new_run(db, data_id: str) -> None:
    """Drop a prior *completed* run's pipeline events before a new run begins."""
    last = await db["pipeline_events"].find_one(
        {"data_id": data_id}, sort=[("seq", -1)], projection={"event": 1},
    )
    if last and last.get("event") in _TERMINAL_EVENTS:
        await db["pipeline_events"].delete_many({"data_id": data_id})
        await db["pipeline_event_seq"].delete_many({"data_id": data_id})


async def consume_loop(
        consumer: KafkaConsumerClient,
        minio: MinIOClient,
        postgres: PostgresClient,
        mongo: MongoClient,
        qdrant_svc: QdrantService,
        graph: GraphStore,
        model: ModelServiceClient,
        docling_parser: InlineDoclingParser,
        producer: KafkaProducerClient | None = None,
) -> None:
    db = mongo.get_client()

    # Per-stage concurrency caps. The consume loop may dispatch up to
    # CONSUME_CONCURRENCY handlers at once; these semaphores then bound how many
    # of each stage actually run in parallel. Silver (CPU/RAM-heavy Docling OCR)
    # is kept low to stay under the worker memory limit; gold (I/O-bound) is high.
    silver_sem = asyncio.Semaphore(cfg.SILVER_CONCURRENCY)
    gold_sem = asyncio.Semaphore(cfg.GOLD_CONCURRENCY)
    conflict_sem = asyncio.Semaphore(cfg.CONFLICT_CONCURRENCY)

    async def handle_silver(event: dict, data_id: str, tenant_id: str, approved_by: str) -> None:
        async with silver_sem:
            try:
                await _reset_events_for_new_run(db, data_id)
                emitter = await PipelineEventEmitter.create(db, data_id)
                await promote_to_silver(
                    minio=minio,
                    postgres=postgres,
                    docling=docling_parser,
                    mongo=mongo,
                    data_id=data_id,
                    tenant_id=tenant_id,
                    approved_by=approved_by,
                    emitter=emitter,
                )
            except Exception as exc:
                await _route_ingestion_failure(
                    producer, stage="silver", source_topic=cfg.KAFKA_TOPIC_SILVER,
                    event=event, exc=exc,
                )

    async def handle_gold(event: dict, data_id: str, tenant_id: str, approved_by: str) -> None:
        async with gold_sem:
            try:
                await _reset_events_for_new_run(db, data_id)
                emitter = await PipelineEventEmitter.create(db, data_id)
                await promote_to_gold(
                    postgres=postgres,
                    qdrant=qdrant_svc.get_client(),
                    graph=graph,
                    mongo=mongo,
                    model=model,
                    data_id=data_id,
                    tenant_id=tenant_id,
                    approved_by=approved_by,
                    emitter=emitter,
                )
            except Exception as exc:
                await _route_ingestion_failure(
                    producer, stage="gold", source_topic=cfg.KAFKA_TOPIC_GOLD,
                    event=event, exc=exc,
                )

    async def handle_batch_resolve(batch_id: str) -> None:
        async with conflict_sem:
            await resolve_batch(batch_id, postgres, qdrant_svc, graph, model, db)

    async def handler(event: dict) -> None:
        stage = event.get("event", "")
        data_id = event.get("data_id", "")
        tenant_id = event.get("tenant_id", "")
        approved_by = event.get("approved_by", "system")

        if stage == "promote_silver":
            if not data_id or not tenant_id:
                raise ValueError(f"promote_silver event missing required fields: {event}")
            await handle_silver(event, data_id, tenant_id, approved_by)
        elif stage == "promote_gold":
            if not data_id or not tenant_id:
                raise ValueError(f"promote_gold event missing required fields: {event}")
            await handle_gold(event, data_id, tenant_id, approved_by)
        elif stage == "resolve_batch":
            batch_id = event.get("batch_id", "")
            if not batch_id:
                raise ValueError(f"resolve_batch event missing batch_id: {event}")
            await handle_batch_resolve(batch_id)
        else:
            raise ValueError(f"unknown event type: {stage!r}")

    await consumer.start_consuming(
        topics=[cfg.KAFKA_TOPIC_SILVER, cfg.KAFKA_TOPIC_GOLD, cfg.KAFKA_TOPIC_CONFLICT_RESOLVE],
        handler=handler,
        concurrency=cfg.CONSUME_CONCURRENCY,
    )
