"""Gold promotion handler: lock the document, run the pipeline, requeue on contention."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from libs.database_connector.postgres_connector import set_current_tenant
from libs.ingestion import config as cfg
from libs.ingestion.consumer import (
    document_name,
    requeue_busy_document,
    reset_events_for_new_run,
    route_ingestion_failure,
)
from libs.ingestion.gold import promote_to_gold
from libs.ingestion.pipeline_events import PipelineEventEmitter
from libs.ingestion.runtime import WorkerRuntime
from libs.shared.document_lock import DocumentBusy, document_lock

log = logging.getLogger(__name__)


def build_gold_handler(runtime: WorkerRuntime) -> Callable[[dict[str, Any]], Awaitable[None]]:
    stage_limit = asyncio.Semaphore(cfg.GOLD_CONCURRENCY)
    db = runtime.mongo.get_client()

    async def handle(event: dict[str, Any]) -> None:
        data_id = str(event.get("data_id", ""))
        tenant_id = str(event.get("tenant_id", ""))
        approved_by = str(event.get("approved_by", ""))
        if not data_id or not tenant_id:
            log.warning("promote_gold: event missing data_id/tenant_id — skipped")
            return

        # See the same fix in kb_promote_silver's handler.py — RLS-scoped
        # reads (postgres.read(ReadJoinRequest(tenant_id=...))) rely on this
        # ContextVar, which nothing else in a Kafka consumer ever sets.
        set_current_tenant(tenant_id)

        async with stage_limit:
            failure: Exception | None = None
            try:
                name = await document_name(runtime.postgres, data_id)
                async with document_lock(runtime.postgres, tenant_id, name):
                    await reset_events_for_new_run(db, data_id)
                    emitter = await PipelineEventEmitter.create(db, data_id)
                    await promote_to_gold(
                        postgres=runtime.postgres,
                        qdrant=runtime.qdrant.get_client(),
                        graph=runtime.graph,
                        mongo=runtime.mongo,
                        model=runtime.model,
                        data_id=data_id,
                        tenant_id=tenant_id,
                        approved_by=approved_by,
                        emitter=emitter,
                    )
                if runtime.result_cache is not None:
                    await runtime.result_cache.invalidate(tenant_id)
            except DocumentBusy:
                await requeue_busy_document(
                    runtime.producer, stage="gold", source_topic=cfg.KAFKA_TOPIC_GOLD,
                    event=event, data_id=data_id,
                )
            except Exception as exc:
                failure = exc
                await route_ingestion_failure(
                    runtime.producer, stage="gold", source_topic=cfg.KAFKA_TOPIC_GOLD,
                    event=event, exc=exc,
                )
            finally:
                if failure is not None:
                    log.error("gold failed data_id=%s: %s", data_id, failure, exc_info=failure)

    return handle
