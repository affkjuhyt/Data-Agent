"""Redis progress events are durable enough for the V3 question stream."""

from __future__ import annotations

import json

import pytest

from services.worker_agent import progress

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int]] = []

    async def xadd(
        self, key: str, fields: dict[str, str], *, maxlen: int, approximate: bool
    ) -> str:
        assert approximate is True
        self.calls.append((key, fields, maxlen))
        return "1-0"


async def test_publish_progress_appends_a_replayable_question_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(progress, "_redis", redis)

    event_id = await progress.publish(
        "01920000-0000-7000-8000-000000000001",
        "planning",
        {"question_id": "q-1"},
    )

    assert event_id == "1-0"
    key, fields, maxlen = redis.calls[0]
    assert key == "sse:01920000-0000-7000-8000-000000000001"
    assert fields["event"] == "planning"
    assert json.loads(fields["data"]) == {"question_id": "q-1"}
    assert maxlen == 100


async def test_progress_is_a_noop_when_redis_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(progress, "_redis", None)
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert await progress.publish("q-1", "planning", {}) is None
