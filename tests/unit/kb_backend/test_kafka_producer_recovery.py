"""KafkaProducerClient.produce recreates an idempotent producer the broker rejects
(OutOfOrderSequenceNumber / UnknownProducerId) and retries once, instead of
failing every later send until the pod restarts."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

pytest.importorskip("aiokafka")

from aiokafka.errors import KafkaError, OutOfOrderSequenceNumber, UnknownProducerId

from libs.database_connector.kafka_connector import KafkaProducerClient


class _Client(KafkaProducerClient):
    """KafkaProducerClient uses __slots__; a plain subclass gets a __dict__ so a test can swap _reconnect."""


def _client(first: AsyncMock, second: AsyncMock | None = None) -> KafkaProducerClient:
    client = _Client()
    client._producer = MagicMock(send_and_wait=first)

    async def reconnect() -> None:
        client._producer = MagicMock(send_and_wait=second) if second is not None else None

    client._reconnect = reconnect  # type: ignore[method-assign,assignment]
    return client


@pytest.mark.asyncio
async def test_a_healthy_send_does_not_rebuild_the_producer() -> None:
    first = AsyncMock()
    client = _client(first)
    original = client._producer
    await client.produce("t", {"a": 1})
    assert client._producer is original
    first.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OutOfOrderSequenceNumber, UnknownProducerId])
async def test_a_rejected_producer_is_recreated_and_the_send_retried(error: type[KafkaError]) -> None:
    first = AsyncMock(side_effect=error())
    second = AsyncMock()
    client = _client(first, second)
    await client.produce("t", {"a": 1}, key="k")
    second.assert_awaited_once_with("t", value={"a": 1}, key="k")


@pytest.mark.asyncio
async def test_other_errors_are_not_retried() -> None:
    first = AsyncMock(side_effect=KafkaError())
    second = AsyncMock()
    client = _client(first, second)
    with pytest.raises(KafkaError):
        await client.produce("t", {})
    second.assert_not_awaited()


@pytest.mark.asyncio
async def test_failing_reconnect_surfaces_the_original_error() -> None:
    client = _client(AsyncMock(side_effect=OutOfOrderSequenceNumber()))
    with pytest.raises(OutOfOrderSequenceNumber):
        await client.produce("t", {})
