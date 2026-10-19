"""KafkaConsumerClient.start_consuming commits an offset only once every message before it in
its partition has been handled, and never commits past a message whose handler failed: the
message is redelivered after a restart instead of being dropped."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.database_connector.kafka_connector import KafkaConsumerClient, KafkaHandlerFailed

pytestmark = pytest.mark.unit


class _FakeConsumer:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self._messages = messages
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.commit = AsyncMock()

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for message in self._messages:
            yield message
            await asyncio.sleep(0)

    def committed(self) -> list[int]:
        return [next(iter(call.args[0].values())) for call in self.commit.await_args_list]


class _Client(KafkaConsumerClient):
    """KafkaConsumerClient uses __slots__; a plain subclass gets a __dict__ for the fake."""

    fake: _FakeConsumer

    def _make_consumer(self, *topics: str, manual_commit: bool = False) -> Any:
        return self.fake


def _client(count: int) -> _Client:
    client = _Client()
    client.fake = _FakeConsumer(
        [SimpleNamespace(topic="t", partition=0, offset=i, value={"i": i}) for i in range(count)]
    )
    return client


async def test_sequential_failure_is_not_committed_and_stops_the_consumer() -> None:
    client = _client(3)

    async def handler(value: dict[str, Any]) -> None:
        if value["i"] == 1:
            raise RuntimeError("dlq publish failed")

    with pytest.raises(KafkaHandlerFailed):
        await client.start_consuming(["t"], handler, handler_attempts=2, retry_backoff_s=0)
    assert client.fake.committed() == [1]


async def test_sequential_transient_failure_is_retried_then_committed() -> None:
    client = _client(1)
    calls = 0

    async def handler(value: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("blip")

    await client.start_consuming(["t"], handler, handler_attempts=3, retry_backoff_s=0)
    assert calls == 2
    assert client.fake.committed() == [1]


async def test_concurrent_commit_waits_for_the_earliest_unfinished_offset() -> None:
    client = _client(3)
    release_first = asyncio.Event()

    async def handler(value: dict[str, Any]) -> None:
        if value["i"] == 0:
            await release_first.wait()

    async def release_later() -> None:
        # Let offsets 1 and 2 finish while 0 is still running.
        for _ in range(20):
            await asyncio.sleep(0)
        assert all(offset == 0 for offset in client.fake.committed())
        release_first.set()

    await asyncio.gather(
        client.start_consuming(["t"], handler, concurrency=4, retry_backoff_s=0),
        release_later(),
    )
    assert client.fake.committed()[-1] == 3


async def test_concurrent_failure_is_never_committed_past() -> None:
    client = _client(3)

    async def handler(value: dict[str, Any]) -> None:
        if value["i"] == 0:
            raise RuntimeError("dlq publish failed")

    with pytest.raises(KafkaHandlerFailed):
        await client.start_consuming(
            ["t"], handler, concurrency=4, handler_attempts=2, retry_backoff_s=0
        )
    assert all(offset == 0 for offset in client.fake.committed())
