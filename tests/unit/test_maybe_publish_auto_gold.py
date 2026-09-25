"""A promotion_mode="auto" source needs no human click for Gold either — Silver's
handler calls this right after promote_to_silver succeeds (see
kb_promote_silver/libs/handler.py). Never raises: a failed publish here must not
fail a Silver run that already succeeded.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from libs.ingestion import config as cfg
from libs.ingestion.consumer import maybe_publish_auto_gold

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def first(self) -> tuple[Any, ...] | None:
        return self._row


class _Session:
    def __init__(self, metadata: dict[str, Any] | None, purpose: str) -> None:
        self._metadata = metadata
        self._purpose = purpose

    async def execute(self, *_: Any, **__: Any) -> _Result:
        # (doc_metadata, is_deleted, purpose) — the columns maybe_publish_auto_gold selects
        return _Result((self._metadata, False, self._purpose) if self._metadata is not None else None)


class _Postgres:
    def __init__(self, metadata: dict[str, Any] | None, purpose: str = "production") -> None:
        self._metadata = metadata
        self._purpose = purpose

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield _Session(self._metadata, self._purpose)


class _FakeProducer:
    def __init__(self) -> None:
        self.produced: list[dict[str, Any]] = []

    async def produce(self, topic: str, value: dict[str, Any]) -> None:
        self.produced.append({"topic": topic, **value})


def test_publishes_gold_for_an_auto_promotion_source() -> None:
    postgres = _Postgres({"promotion_mode": "auto", "neo4j_connection_id": "n1"})
    producer = _FakeProducer()

    published = asyncio.run(maybe_publish_auto_gold(postgres, producer, data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is True
    assert producer.produced == [{
        "topic": cfg.KAFKA_TOPIC_GOLD, "event": "promote_gold",
        "data_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "neo4j_connection_id": "n1", "approved_by": "web-crawler-worker",
    }]


def test_does_nothing_for_a_manual_promotion_source() -> None:
    postgres = _Postgres({"promotion_mode": "manual"})
    producer = _FakeProducer()

    published = asyncio.run(maybe_publish_auto_gold(postgres, producer, data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is False
    assert producer.produced == []


def test_does_nothing_when_there_is_no_producer() -> None:
    postgres = _Postgres({"promotion_mode": "auto"})

    published = asyncio.run(maybe_publish_auto_gold(postgres, None, data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is False


def test_a_missing_data_row_is_treated_as_manual() -> None:
    postgres = _Postgres(None)
    producer = _FakeProducer()

    published = asyncio.run(maybe_publish_auto_gold(postgres, producer, data_id="33333333-3333-3333-3333-333333333333", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is False
    assert producer.produced == []


def test_a_publish_failure_is_swallowed_not_raised() -> None:
    postgres = _Postgres({"promotion_mode": "auto"})

    class _Boom:
        async def produce(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("kafka down")

    published = asyncio.run(maybe_publish_auto_gold(postgres, _Boom(), data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is False


@pytest.mark.parametrize("purpose", ["training", "evaluation"])
def test_training_and_evaluation_data_never_auto_promote_to_gold(purpose: str) -> None:
    postgres = _Postgres({"promotion_mode": "auto"}, purpose=purpose)
    producer = _FakeProducer()

    published = asyncio.run(maybe_publish_auto_gold(postgres, producer, data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222"))

    assert published is False
    assert producer.produced == []
