"""The api's half of the REST try channel: the request waits in Redis a minute at most, the
answer is taken once, and both keys are gone whatever happened."""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.api.modules.catalog.rest_try import QUEUE, RedisRestTries, TriesBusy

pytestmark = [pytest.mark.unit]


class FakeRedis:
    def __init__(self, answer: dict[str, Any] | None, waiting: int = 0) -> None:
        self.answer = answer
        self.values: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.queue: list[str] = ["other"] * waiting
        self.deleted: list[str] = []

    async def llen(self, key: str) -> int:
        return len(self.queue) if key == QUEUE else 0

    def pipeline(self, transaction: bool = True) -> FakeRedis:
        return self

    async def __aenter__(self) -> FakeRedis:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def set(self, key: str, value: str, ex: int) -> None:
        self.values[key], self.ttl[key] = value, ex

    def lpush(self, key: str, value: str) -> None:
        self.queue.insert(0, value)

    async def execute(self) -> None:
        return None

    async def blpop(self, keys: list[str], **options: Any) -> tuple[str, str] | None:
        if self.answer is None:
            return None
        return keys[0], json.dumps(self.answer)

    async def delete(self, *keys: str) -> None:
        self.deleted += keys
        for key in keys:
            self.values.pop(key, None)


JOB = {"id": "t-1", "org_id": "o", "request": {"url": "https://api.example.vn"}}


async def test_the_request_is_queued_briefly_and_the_answer_returned() -> None:
    redis = FakeRedis({"ok": {"status": 200}})
    answer = await RedisRestTries(redis).run(JOB, wait_seconds=1)
    assert answer == {"ok": {"status": 200}}
    assert redis.queue == ["t-1"] and redis.ttl["connector:try:t-1"] == 60
    assert redis.values == {}
    assert redis.deleted == ["connector:try:t-1", "connector:try:t-1:result"]


async def test_no_answer_in_time_is_none_and_leaves_nothing() -> None:
    redis = FakeRedis(None)
    assert await RedisRestTries(redis).run(JOB, wait_seconds=1) is None
    assert redis.values == {}


async def test_a_full_queue_is_busy() -> None:
    with pytest.raises(TriesBusy):
        await RedisRestTries(FakeRedis(None, waiting=3), max_waiting=3).run(JOB, wait_seconds=1)
