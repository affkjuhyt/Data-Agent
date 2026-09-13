"""Structured-import handler.

propose_structured_table marks the job 'failed' itself — a user-visible terminal
state retried from the review screen — so there is nothing to requeue here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from libs.ingestion import config as cfg
from libs.ingestion.runtime import WorkerRuntime
from services.kb_structured_data.libs.propose import propose_structured_table

log = logging.getLogger(__name__)


def build_structured_handler(runtime: WorkerRuntime) -> Callable[[dict[str, Any]], Awaitable[None]]:
    stage_limit = asyncio.Semaphore(cfg.SILVER_CONCURRENCY)

    async def handle(event: dict[str, Any]) -> None:
        data_id = str(event.get("data_id", ""))
        job_id = str(event.get("job_id", ""))
        tenant_id = str(event.get("tenant_id", ""))
        if not data_id or not job_id or not tenant_id:
            log.warning("structured_data: event missing data_id/job_id/tenant_id — skipped")
            return

        async with stage_limit:
            await propose_structured_table(
                minio=runtime.minio,
                postgres=runtime.postgres,
                docling=runtime.docling_parser,
                mongo=runtime.mongo,
                data_id=data_id,
                job_id=job_id,
                tenant_id=tenant_id,
            )

    return handle
