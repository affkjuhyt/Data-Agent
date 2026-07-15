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

from utils import config as cfg
from utils.docling_parser import InlineDoclingParser
from utils.graph_store import GraphStore
from utils.log_writer import write_log
from utils.pipeline_events import PipelineEventEmitter
from silver import promote_to_silver
from gold import promote_to_gold
from conflict_resolver import fetch_awaiting_conflict, _resolve_conflict

log = logging.getLogger(__name__)

_TERMINAL_EVENTS = ("pipeline.done", "pipeline.error")


async def _route_ingestion_failure(
    producer: KafkaProducerClient | None,
    *,
    stage: str,
    source_topic: str,
    event: dict,
    exc: Exception,
) -> None:
    """Retry-or-dead-letter a failed silver/gold document instead of dropping it.

    A stage exception used to be swallowed while the Kafka offset committed anyway,
    stranding the document. Now we re-publish it — up to INGESTION_MAX_ATTEMPTS
    times to the SAME stage topic (bounded in-band retry), then once to the DLQ
    topic — so the failure is replayable, not terminal. Best-effort: if the
    producer is unavailable the document is still marked FAILED in Mongo (visible +
    manually re-promotable), so we degrade rather than crash the consume loop.
    """
    attempts = int(event.get("attempts", 0)) + 1
    payload = {**event, "attempts": attempts}
    data_id = event.get("data_id", "")
    if producer is None:
        log.error("Worker: no producer — cannot DLQ/retry %s data_id=%s (attempt %d)",
                  stage, data_id, attempts)
        return
    try:
        if attempts < cfg.INGESTION_MAX_ATTEMPTS:
            await producer.produce(source_topic, value=payload, key=data_id or None)
            log.warning("Worker: %s data_id=%s failed (%s) — requeued attempt %d/%d",
                        stage, data_id, exc, attempts, cfg.INGESTION_MAX_ATTEMPTS)
        else:
            dlq_payload = {
                **payload,
                "failed_stage": stage,
                "error": str(exc)[:500],
                "source_topic": source_topic,
            }
            await producer.produce(cfg.KAFKA_TOPIC_DLQ, value=dlq_payload, key=data_id or None)
            log.error("Worker: %s data_id=%s exhausted %d attempts — routed to DLQ %s",
                      stage, data_id, attempts, cfg.KAFKA_TOPIC_DLQ)
    except Exception as route_exc:  # producer down / broker unreachable
        log.critical("Worker: FAILED to DLQ/retry %s data_id=%s: %s (document stranded "
                     "as FAILED — replay manually)", stage, data_id, route_exc)


async def _reset_events_for_new_run(db, data_id: str) -> None:
    """Drop a prior *completed* run's pipeline events before a new run begins.

    Events accumulate per data_id (the emitter just resumes the seq counter), so a
    re-promotion leaves the previous run's terminal event (pipeline.done/error)
    sitting mid-stream. The SSE replay returns at the first terminal it sees, which
    would otherwise freeze the observer on the stale run when the page is refreshed
    mid-pipeline. A run is "new" when the latest stored event is a terminal; the
    silver→gold continuation (silver ends on silver.complete, not a terminal) is
    left untouched so its single stream stays intact.
    """
    try:
        last = await db["pipeline_events"].find_one(
            {"data_id": data_id}, sort=[("seq", -1)], projection={"event": 1},
        )
        if last and last.get("event") in _TERMINAL_EVENTS:
            await db["pipeline_events"].delete_many({"data_id": data_id})
            await db["pipeline_event_seq"].delete_many({"data_id": data_id})
            log.info("Worker: cleared prior completed-run events for data_id=%s", data_id)
    except Exception as exc:
        log.warning("Worker: pipeline-event reset failed data_id=%s: %s", data_id, exc)


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
    silver_sem   = asyncio.Semaphore(cfg.SILVER_CONCURRENCY)
    gold_sem     = asyncio.Semaphore(cfg.GOLD_CONCURRENCY)
    conflict_sem = asyncio.Semaphore(cfg.CONFLICT_CONCURRENCY)

    async def handle_silver(event: dict, data_id: str, tenant_id: str, approved_by: str) -> None:
        async with silver_sem:
            # Update status/stage but PRESERVE the logs array so the document's
            # full ingestion history is kept across Bronze→Silver→Gold transitions.
            try:
                await db["kb_ingestion_logs"].update_one(
                    {"data_id": data_id},
                    {"$set": {"status": "RUNNING", "stage": "silver",
                              "updated_at": __import__("datetime").datetime.utcnow()},
                     "$setOnInsert": {"created_at": __import__("datetime").datetime.utcnow()}},
                    upsert=True,
                )
            except Exception:
                pass
            await write_log(db, data_id, "RUNNING", "Silver stage started")
            await _reset_events_for_new_run(db, data_id)
            emitter = await PipelineEventEmitter.create(db, data_id)
            try:
                await write_log(db, data_id, "CHUNKING", "Parsing document with Docling")
                result = await promote_to_silver(
                    minio=minio,
                    postgres=postgres,
                    docling=docling_parser,
                    mongo=mongo,
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
                await _route_ingestion_failure(
                    producer, stage="silver", source_topic=cfg.KAFKA_TOPIC_SILVER,
                    event=event, exc=exc)

    async def handle_gold(event: dict, data_id: str, tenant_id: str, approved_by: str) -> None:
        async with gold_sem:
            # Preserve prior (Silver) log entries — only update status/stage so the
            # Gold logs append onto the document's existing history.
            try:
                await db["kb_ingestion_logs"].update_one(
                    {"data_id": data_id},
                    {"$set": {"status": "GRAPH_EXTRACTING", "stage": "gold",
                              "updated_at": __import__("datetime").datetime.utcnow()},
                     "$setOnInsert": {"created_at": __import__("datetime").datetime.utcnow()}},
                    upsert=True,
                )
            except Exception:
                pass
            await write_log(db, data_id, "GRAPH_EXTRACTING", "Gold stage started — extracting entities")
            await _reset_events_for_new_run(db, data_id)
            emitter = await PipelineEventEmitter.create(db, data_id)
            try:
                result = await promote_to_gold(
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
                await _route_ingestion_failure(
                    producer, stage="gold", source_topic=cfg.KAFKA_TOPIC_GOLD,
                    event=event, exc=exc)

    async def handle_conflict_resolve(conflict_id: str) -> None:
        async with conflict_sem:
            conflict = await fetch_awaiting_conflict(postgres, conflict_id)
            if conflict is None:
                log.warning(
                    "Worker: conflict_id=%s not found or already resolved — skipping",
                    conflict_id,
                )
                return
            await _resolve_conflict(conflict, postgres, qdrant_svc, graph, model)

    async def handler(event: dict) -> None:
        stage       = event.get("event", "")
        data_id     = event.get("data_id", "")
        tenant_id   = event.get("tenant_id", "")
        approved_by = event.get("approved_by", "system")

        if stage == "promote_silver":
            if not data_id or not tenant_id:
                log.warning("Worker: promote_silver event missing required fields: %s", event)
                return
            await handle_silver(event, data_id, tenant_id, approved_by)
        elif stage == "promote_gold":
            if not data_id or not tenant_id:
                log.warning("Worker: promote_gold event missing required fields: %s", event)
                return
            await handle_gold(event, data_id, tenant_id, approved_by)
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
