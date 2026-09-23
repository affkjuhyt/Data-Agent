"""Kafka consume helpers shared by the ingestion worker processes."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select as sa_select

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from libs.database_connector.kafka_connector import KafkaProducerClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion import config as cfg

log = logging.getLogger(__name__)

TERMINAL_EVENTS = ("pipeline.done", "pipeline.error")


async def route_ingestion_failure(
    producer: KafkaProducerClient | None,
    *,
    stage: str,
    source_topic: str,
    event: dict[str, Any],
    exc: Exception,
) -> None:
    """Retry-or-dead-letter a failed document instead of dropping it."""
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
    await producer.produce(
        cfg.KAFKA_TOPIC_DLQ,
        value={**payload, "failed_stage": stage, "error": str(exc)[:500], "source_topic": source_topic},
        key=data_id or None,
    )


async def requeue_busy_document(
    producer: KafkaProducerClient | None,
    *,
    stage: str,
    source_topic: str,
    event: dict[str, Any],
    data_id: str,
) -> None:
    """Contention is not a failure — requeue without spending a retry attempt."""
    waits = int(event.get("busy_waits", 0)) + 1
    log.info("%s data_id=%s is locked by another stage — requeue %d", stage, data_id, waits)
    if producer is None:
        return
    if waits > cfg.INGESTION_MAX_ATTEMPTS:
        await producer.produce(
            cfg.KAFKA_TOPIC_DLQ,
            value={**event, "failed_stage": stage, "error": "document locked by another stage"},
            key=data_id or None,
        )
        return
    await producer.produce(source_topic, value={**event, "busy_waits": waits}, key=data_id or None)


async def document_name(postgres: PostgresClient, data_id: str) -> str:
    async with postgres.get_client() as session:
        name = await session.scalar(
            sa_select(KBDataORM.name).where(KBDataORM.data_id == uuid.UUID(data_id))
        )
    return name or ""


async def maybe_publish_auto_gold(
    postgres: PostgresClient, producer: KafkaProducerClient | None, *, data_id: str, tenant_id: str,
) -> bool:
    """A scheduled source with `promotion_mode: "auto"` (stamped into
    `KBData.metadata` by the crawler worker at Bronze time, see
    `platform_workers/workers/web_crawler/bronze.py`) needs no human click
    for Gold — a worker can't click a button. Called right after Silver
    succeeds; publishes the same `promote_gold` event kb_backend's own
    `/promote/{id}/gold` route builds. Returns whether it published, purely
    for logging — never raises, since a failed auto-promote must not fail
    the Silver run that already succeeded (the document simply stays at
    Silver until the next successful auto-eligible run, or a manual click)."""
    if producer is None:
        return False
    async with postgres.get_client() as session:
        row = (await session.execute(
            sa_select(KBDataORM.doc_metadata, KBDataORM.is_deleted).where(KBDataORM.data_id == uuid.UUID(data_id))
        )).first()
    if row is None or row[1]:
        # Deleted (moved to Trash) after Silver succeeded but before this
        # ran — a worker auto-promoting a document a steward just deleted
        # would resurrect it at Gold; nothing to do here instead.
        return False
    metadata = row[0] or {}
    if metadata.get("promotion_mode") != "auto":
        return False
    try:
        await producer.produce(
            topic=cfg.KAFKA_TOPIC_GOLD,
            value={
                "event": "promote_gold",
                "data_id": data_id,
                "tenant_id": tenant_id,
                "neo4j_connection_id": metadata.get("neo4j_connection_id", ""),
                "approved_by": "web-crawler-worker",
            },
        )
    except Exception:  # noqa: BLE001 — a failed auto-promote must not fail the Silver run that already succeeded
        log.warning("maybe_publish_auto_gold: failed to queue gold for data_id=%s", data_id, exc_info=True)
        return False
    return True


async def reset_events_for_new_run(db: Any, data_id: str) -> None:
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
        if last and last.get("event") in TERMINAL_EVENTS:
            await db["pipeline_events"].delete_many({"data_id": data_id})
            await db["pipeline_event_seq"].delete_many({"data_id": data_id})
            log.info("Worker: cleared prior completed-run events for data_id=%s", data_id)
    except Exception as exc:
        log.warning("Worker: pipeline-event reset failed data_id=%s: %s", data_id, exc)
