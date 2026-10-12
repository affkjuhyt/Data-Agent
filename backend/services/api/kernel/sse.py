"""Server-Sent Events (A1.6): questions, agent runs and forecasts stream
progress as text/event-stream.

Every event carries an `id`, so a client that drops (a proxy timeout, a phone
changing network) reconnects with Last-Event-ID and the producer resumes after
that event instead of replaying the stream or losing the answer (ADR-29).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Header
from fastapi.responses import StreamingResponse

MEDIA_TYPE = "text/event-stream"

# A comment line every 15 s keeps idle proxies (Kong, nginx: 60 s) from closing
# a stream while the agent is still thinking.
HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class Event:
    id: str
    event: str
    data: Any


def format_event(event: Event) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"), default=str)
    # One data line: json.dumps never emits a raw newline, which would split the event.
    return f"id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n"


HEARTBEAT = ": keep-alive\n\n"


def last_event_id(
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=128)] = None,
) -> str | None:
    return last_event_id or None


async def _encode(events: AsyncIterator[Event | None]) -> AsyncIterator[str]:
    async for event in events:
        # Producers yield None when they waited HEARTBEAT_SECONDS with nothing to say.
        yield HEARTBEAT if event is None else format_event(event)


def event_stream(events: AsyncIterator[Event | None]) -> StreamingResponse:
    return StreamingResponse(
        _encode(events),
        media_type=MEDIA_TYPE,
        # no-transform and X-Accel-Buffering stop proxies from buffering the
        # stream into one late chunk.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
