"""Silver promotion handler: lock the document, run the pipeline, requeue on contention."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from libs.ingestion import config as cfg
from libs.ingestion.consumer import (
    document_name,
    requeue_busy_document,
    reset_events_for_new_run,
    route_ingestion_failure,
)
from libs.ingestion.pipeline_events import PipelineEventEmitter
from libs.ingestion.runtime import WorkerRuntime
from libs.ingestion.silver import promote_to_silver
from libs.shared.document_lock import DocumentBusy, document_lock

log = logging.getLogger(__name__)


def build_silver_handler(runtime: WorkerRuntime) -> Callable[[dict[str, Any]], Awaitable[None]]:
    stage_limit = asyncio.Semaphore(cfg.SILVER_CONCURRENCY)
    db = runtime.mongo.get_client()

    async def handle(event: dict[str, Any]) -> None:
        data_id = str(event.get("data_id", ""))
        tenant_id = str(event.get("tenant_id", ""))
        approved_by = str(event.get("approved_by", ""))
        if not data_id or not tenant_id:
            log.warning("promote_silver: event missing data_id/tenant_id — skipped")
            return

        async with stage_limit:
            failure: Exception | None = None
            try:
                name = await document_name(runtime.postgres, data_id)
                async with document_lock(runtime.postgres, tenant_id, name):
                    await reset_events_for_new_run(db, data_id)
                    emitter = await PipelineEventEmitter.create(db, data_id)
                    await promote_to_silver(
                        minio=runtime.minio,
                        postgres=runtime.postgres,
                        docling=runtime.docling_parser,
                        mongo=runtime.mongo,
                        data_id=data_id,
                        tenant_id=tenant_id,
                        approved_by=approved_by,
                        emitter=emitter,
                    )
            except DocumentBusy:
                await requeue_busy_document(
                    runtime.producer, stage="silver", source_topic=cfg.KAFKA_TOPIC_SILVER,
                    event=event, data_id=data_id,
                )
            except Exception as exc:
                failure = exc
                await route_ingestion_failure(
                    runtime.producer, stage="silver", source_topic=cfg.KAFKA_TOPIC_SILVER,
                    event=event, exc=exc,
                )
            finally:
                if failure is not None:
                    log.error("silver failed data_id=%s: %s", data_id, failure, exc_info=failure)

    return handle
