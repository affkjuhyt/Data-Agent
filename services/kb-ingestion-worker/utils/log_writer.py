"""IngestionLogger — the single logging entry point for the orchestrator functions
(main, promote_to_silver, promote_to_gold, resolve_batch). Writes per-document
progress/log entries to MongoDB kb_ingestion_logs and mirrors to the console logger."""
from __future__ import annotations

from typing import Any
import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_console = logging.getLogger("kb-ingestion-worker")


class IngestionLogger:
    def __init__(self, db=None, data_id: str = "") -> None:
        self.db = db
        self.data_id = data_id

    async def logging(
        self,
        message: str,
        *,
        level: str = "INFO",
        status: str | None = None,
        stage: str | None = None,
    ) -> None:
        getattr(_console, level.lower())(message)
        if self.db is None or not self.data_id:
            return
        now = datetime.datetime.utcnow()
        fields: dict = {"updated_at": now}
        if status is not None:
            fields["status"] = status
        if stage is not None:
            fields["stage"] = stage
        await self.db["kb_ingestion_logs"].update_one(
            {"data_id": self.data_id},
            {
                "$set": fields,
                "$push": {"logs": {"timestamp": now, "level": level, "message": message}},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def progress(
        self,
        *,
        total: int | None = None,
        processed: int | None = None,
        status: str | None = None,
    ) -> None:
        if self.db is None or not self.data_id:
            return
        fields: dict = {}
        if total is not None:
            fields["total_chunks"] = total
        if processed is not None:
            fields["processed_chunks"] = processed
        if status is not None:
            fields["status"] = status
        if not fields:
            return
        await self.db["kb_ingestion_logs"].update_one(
            {"data_id": self.data_id}, {"$set": fields}, upsert=True,
        )
