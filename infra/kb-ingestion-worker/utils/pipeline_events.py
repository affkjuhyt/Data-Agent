"""Per-document pipeline event emitter backed by MongoDB.

Workers call ``await emitter.emit(event, payload)`` at each processing step.
The SSE endpoint in the backend polls ``pipeline_events`` sorted by seq so the
browser can display every detail in real time.

Collection schema:
    pipeline_events:  {data_id, seq, event, ts, payload}
    pipeline_event_seq: {data_id, seq}  — atomic counter per document
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

log = logging.getLogger(__name__)


class PipelineEventEmitter:
    """Writes structured events to MongoDB ``pipeline_events``.

    Uses an atomic per-document counter stored in ``pipeline_event_seq`` so
    events from separate silver and gold Kafka messages are sequenced correctly
    even though they run in different worker invocations.

    Usage::

        emitter = await PipelineEventEmitter.create(mongo_db, data_id)
        await emitter.emit("silver.chunk.found", {"index": 0, "preview": "..."})
    """

    def __init__(self, db, data_id: str, seq: int) -> None:
        self._db = db
        self._data_id = data_id
        self._seq = seq

    @classmethod
    async def create(cls, db, data_id: str) -> "PipelineEventEmitter":
        """Instantiate and resume from the last known seq for this data_id."""
        try:
            last = await db["pipeline_events"].find_one(
                {"data_id": data_id},
                sort=[("seq", -1)],
                projection={"seq": 1},
            )
            seq = last["seq"] if last else 0
        except Exception as exc:
            log.warning("PipelineEventEmitter.create: seq lookup failed (%s) — starting at 0", exc)
            seq = 0
        return cls(db, data_id, seq)

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self._seq += 1
        try:
            await self._db["pipeline_events"].insert_one({
                "data_id": self._data_id,
                "seq":     self._seq,
                "event":   event,
                "ts":      datetime.datetime.utcnow(),
                "payload": payload or {},
            })
        except Exception as exc:
            log.warning(
                "PipelineEventEmitter.emit dropped event=%r data_id=%s: %s",
                event, self._data_id, exc,
            )
