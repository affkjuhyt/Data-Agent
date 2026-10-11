"""The API can replay worker-agent progress from the Redis question stream."""

from __future__ import annotations

import json

import pytest

from services.api.modules.qa import progress

pytestmark = pytest.mark.unit


class FakeRedis:
    async def xread(self, streams: dict[str, str], *, count: int, block: int):
        assert streams == {"sse:q-1": "0-0"}
        assert count == 100 and block == 1_500
        return [
            (
                "sse:q-1",
                [("1-0", {"event": "planning", "data": json.dumps({"question_id": "q-1"})})],
            )
        ]


async def test_read_progress_maps_redis_stream_entries_to_sse_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(progress, "_client", lambda: FakeRedis())

    events = await progress.read("q-1")

    assert len(events) == 1
    assert events[0].id == "1-0"
    assert events[0].event == "planning"
    assert events[0].data == {"question_id": "q-1"}


async def test_read_progress_returns_nothing_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "_client", lambda: None)

    assert await progress.read("q-1") == []
