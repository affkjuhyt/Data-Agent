"""Writes per-document ingestion progress to MongoDB kb_ingestion_logs."""
from __future__ import annotations

from typing import Any
import datetime
import logging

log = logging.getLogger(__name__)


async def set_chunk_progress(db: Any, data_id: str, *, total: int | None = None,
                             processed: int | None = None, status: str | None = None) -> None:
    """Write progress onto the kb_ingestion_logs doc that the /progress SSE reads, so the
    percentage is computed from a single Mongo source (no cross-store Postgres count).
    `total`/`processed` drive the per-chunk band; `status` marks gold's post-chunk doc-level
    phases (reduce/relationships/graph) so the bar keeps moving past the chunk band instead
    of flat-lining while the slow doc-level LLM extraction runs."""
    upd: dict[str, Any] = {}
    if total is not None:
        upd["total_chunks"] = total
    if processed is not None:
        upd["processed_chunks"] = processed
    if status is not None:
        upd["status"] = status
    if not upd:
        return
    try:
        await db["kb_ingestion_logs"].update_one(
            {"data_id": data_id}, {"$set": upd}, upsert=True,
        )
    except Exception as exc:
        log.warning("Failed to write chunk progress data_id=%s: %s", data_id, exc)


async def write_log(db: Any, data_id: str, status: str, message: str, level: str = "INFO") -> None:
    now = datetime.datetime.utcnow()
    try:
        await db["kb_ingestion_logs"].update_one(
            {"data_id": data_id},
            {
                "$set":         {"status": status, "updated_at": now},
                "$push":        {"logs": {"timestamp": now, "level": level, "message": message}},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except Exception as exc:
        log.warning("Failed to write ingestion log data_id=%s: %s", data_id, exc)
