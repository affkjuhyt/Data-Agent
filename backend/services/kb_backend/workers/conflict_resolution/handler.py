"""Conflict batch handler: resolve one batch per message."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from libs.ingestion import config as cfg
from libs.ingestion.runtime import WorkerRuntime
from services.kb_backend.workers.conflict_resolution.orchestrator import resolve_batch

log = logging.getLogger(__name__)


def build_conflict_handler(runtime: WorkerRuntime) -> Callable[[dict[str, Any]], Awaitable[None]]:
    stage_limit = asyncio.Semaphore(cfg.CONFLICT_CONCURRENCY)
    db = runtime.mongo.get_client()

    async def handle(event: dict[str, Any]) -> None:
        batch_id = str(event.get("batch_id", ""))
        if not batch_id:
            log.warning("conflict_resolution: event missing batch_id — skipped")
            return

        async with stage_limit:
            try:
                await resolve_batch(
                    batch_id, runtime.postgres, runtime.qdrant, runtime.require_graph(), runtime.model, db
                )
            except Exception as exc:
                log.error("conflict batch %s failed: %s", batch_id, exc, exc_info=exc)

    return handle
