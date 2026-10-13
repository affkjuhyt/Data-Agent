"""The private-question channel (UC-16-02, BR-193) against an in-memory stand-in for Redis."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.api.modules.exec.ports import ModelUnavailable, PrivateQuestion
from services.api.modules.exec.private_channel import QUEUE, TTL_SECONDS, RedisPrivateQuestions

pytestmark = pytest.mark.unit


class _Pipe:
    def __init__(self, redis: _Redis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> _Pipe:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def set(self, *args: Any, **kwargs: Any) -> None:
        self.ops.append(("set", args, kwargs))

    def lpush(self, *args: Any, **kwargs: Any) -> None:
        self.ops.append(("lpush", args, kwargs))

    async def execute(self) -> None:
        for name, args, kwargs in self.ops:
            await getattr(self.redis, name)(*args, **kwargs)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    def pipeline(self, transaction: bool = True) -> _Pipe:
        return _Pipe(self)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.ttl[key] = ex

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def xrange(self, key: str, start: str, end: str) -> list[tuple[str, dict[str, str]]]:
        return self.streams.get(key, [])

    def xadd(self, key: str, event: str, data: dict[str, Any]) -> None:
        entries = self.streams.setdefault(key, [])
        entries.append((f"1-{len(entries)}", {"event": event, "data": json.dumps(data)}))


ORG, OWNER, SPACE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _question(**extra: Any) -> PrivateQuestion:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "org_id": ORG,
        "owner_id": OWNER,
        "text": "Biên lãi Q4 có giữ được không?",
        "context": ["Ưu tiên: giữ NIM trên 3%"],
        "as_of": None,
        "data_space_id": SPACE,
    }
    values.update(extra)
    return PrivateQuestion(**values)


async def test_a_question_is_held_15_minutes_and_queued_for_the_worker() -> None:
    redis = _Redis()
    channel = RedisPrivateQuestions(redis)
    question = _question()
    await channel.submit(question)
    key = f"exec:pq:{question.id}"
    assert redis.ttl[key] == TTL_SECONDS == 900
    assert redis.lists[QUEUE] == [str(question.id)]
    held = json.loads(redis.values[key])
    assert held == {
        "id": str(question.id),
        "org_id": str(ORG),
        "owner_id": str(OWNER),
        "text": question.text,
        "context": question.context,
        "as_of": None,
        "data_space_id": str(SPACE),
    }


async def test_its_asker_reads_the_answer_numbered_from_one() -> None:
    redis = _Redis()
    channel = RedisPrivateQuestions(redis)
    question = _question()
    await channel.submit(question)
    redis.xadd(f"exec:pq:{question.id}:events", "progress", {"step": "query"})
    redis.xadd(f"exec:pq:{question.id}:events", "answer", {"text": "Có", "figures": []})
    events = await channel.events(org_id=ORG, owner_id=OWNER, question_id=question.id)
    assert events is not None
    assert [(e.id, e.event) for e in events] == [("1", "progress"), ("2", "answer")]


async def test_another_user_or_an_expired_question_reads_nothing() -> None:
    redis = _Redis()
    channel = RedisPrivateQuestions(redis)
    question = _question()
    await channel.submit(question)
    other = await channel.events(org_id=ORG, owner_id=uuid.uuid4(), question_id=question.id)
    foreign = await channel.events(org_id=uuid.uuid4(), owner_id=OWNER, question_id=question.id)
    gone = await channel.events(org_id=ORG, owner_id=OWNER, question_id=uuid.uuid4())
    assert other is None and foreign is None and gone is None


async def test_a_full_queue_is_model_unavailable_not_a_longer_wait() -> None:
    redis = _Redis()
    channel = RedisPrivateQuestions(redis, max_waiting=1)
    await channel.submit(_question())
    with pytest.raises(ModelUnavailable):
        await channel.submit(_question())
    assert len(redis.lists[QUEUE]) == 1
