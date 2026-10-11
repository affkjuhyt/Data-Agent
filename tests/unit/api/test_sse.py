"""SSE framing (A1.6): one event per id, resumable by Last-Event-ID."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel.sse import HEARTBEAT, Event, event_stream, format_event, last_event_id

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_event_is_one_data_line() -> None:
    text = format_event(Event(id="7", event="answer", data={"text": "dòng 1\ndòng 2"}))
    assert text == 'id: 7\nevent: answer\ndata: {"text":"dòng 1\\ndòng 2"}\n\n'


def test_stream_resumes_after_last_event_id() -> None:
    app = create_app(Settings())
    log = [Event(id=str(i), event="progress", data={"step": i}) for i in range(1, 4)]

    @app.get("/stream")
    async def stream(after: Annotated[str | None, Depends(last_event_id)]):  # type: ignore[no-untyped-def]
        async def events() -> AsyncIterator[Event | None]:
            for e in log:
                if after is None or int(e.id) > int(after):
                    yield e
            yield None

        return event_stream(events())

    r = TestClient(app).get("/stream", headers={"Last-Event-ID": "1"})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache, no-transform"
    assert r.text == format_event(log[1]) + format_event(log[2]) + HEARTBEAT
