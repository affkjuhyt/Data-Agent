"""Per-document pipeline event emitter backed by MongoDB.

Workers call ``await emitter.emit(event, payload)`` at each processing step, or wrap
a whole step in ``async with emitter.step("silver.parse") as event_scope:`` — the
context manager emits ``<name>.start`` on entry, ``<name>.complete`` on success, and
``<name>.error`` on exception, so step functions never write start/complete emits by
hand. The SSE endpoint in the backend polls ``pipeline_events`` sorted by sequence so
the browser can display every detail in real time.

Collection schema:
    pipeline_events:  {data_id, sequence, event, ts, payload}
    pipeline_event_seq: {data_id, sequence}  — atomic counter per document
"""
from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class StepScope:
    """Handle yielded by ``PipelineEventEmitter.step`` so a step can attach the
    payload reported on its ``.complete`` event after the work is done."""

    __slots__ = ("payload",)

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}

    def set(self, **fields: Any) -> "StepScope":
        self.payload.update(fields)
        return self


class PipelineEventEmitter:
    """Writes structured events to MongoDB ``pipeline_events``.

    Sequenced by an atomic counter in ``pipeline_event_seq``, so silver and gold —
    separate Kafka messages in separate worker invocations — still order correctly.
    """

    def __init__(self, database, data_id: str, sequence: int) -> None:
        self._database = database
        self._data_id = data_id
        self._sequence = sequence

    @classmethod
    async def create(cls, database, data_id: str) -> "PipelineEventEmitter":
        """Instantiate and resume from the last known sequence for this data_id."""
        latest_event = await database["pipeline_events"].find_one(
            {"data_id": data_id},
            sort=[("seq", -1)],
            projection={"seq": 1},
        )
        sequence = latest_event["seq"] if latest_event else 0
        return cls(database, data_id, sequence)

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self._sequence += 1
        try:
            await self._database["pipeline_events"].insert_one({
                "data_id": self._data_id,
                "seq":     self._sequence,
                "event":   event,
                "ts":      datetime.datetime.utcnow(),
                "payload": payload or {},
            })
        except Exception as exc:
            raise RuntimeError(
                f"pipeline event insert failed event={event!r} data_id={self._data_id}: {exc}"
            ) from exc

    @asynccontextmanager
    async def step(self, name: str, **start_payload: Any) -> AsyncIterator[StepScope]:
        """Emit ``name.start`` now, ``name.complete`` (with the scope payload) on
        success, or ``name.error`` if the wrapped block raises (then re-raise)."""
        scope = StepScope()
        await self.emit(f"{name}.start", dict(start_payload))
        try:
            yield scope
        except Exception as exc:
            await self.emit(f"{name}.error", {"error": str(exc)[:300], **scope.payload})
            raise
        await self.emit(f"{name}.complete", scope.payload)


class _NullEmitter(PipelineEventEmitter):
    """No-op emitter so step functions can always assume an emitter is present
    (no ``if emitter:`` guards) even when events aren't being recorded."""

    def __init__(self) -> None:
        super().__init__(None, "", 0)

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        return


NULL_EMITTER = _NullEmitter()
