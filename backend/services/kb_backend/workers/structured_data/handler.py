"""Structured-import handler.

propose_structured_table marks the job 'failed' itself — a user-visible terminal
state retried from the review screen — so there is nothing to requeue here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from libs.database_connector.postgres_connector import set_current_tenant
from libs.ingestion import config as cfg
from libs.ingestion.runtime import WorkerRuntime
from services.kb_backend.workers.structured_data.propose import propose_structured_table

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

        # See the same fix in the promote_silver worker handler.py — RLS-scoped
        # reads (postgres.read(ReadJoinRequest(tenant_id=...))) rely on this
        # ContextVar, which nothing else in a Kafka consumer ever sets.
        set_current_tenant(tenant_id)

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
