"""Writes per-document ingestion progress to MongoDB kb_ingestion_logs."""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger(__name__)


async def write_log(db, data_id: str, status: str, message: str, level: str = "INFO") -> None:
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
