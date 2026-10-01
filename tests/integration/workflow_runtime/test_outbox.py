"""libs/shared/outbox.py against a real migrated Postgres.

What has to hold: an event survives the broker being down and is delivered once it is
back, in order; two relays never publish the same row; a poison event is parked instead
of blocking the ones behind it; and an event enqueued in a transaction that rolls back
never exists.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from libs.basemodel.services_databaseconnector.postgres_orm.outbox_orm import OutboxEventORM
from tests.integration._db import reset_database

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


@pytest.fixture
def migrated_db(postgres_container) -> Iterator[dict[str, str]]:
    reset_database(postgres_container["dsn"])
    dsn = postgres_container["dsn"]
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://" + dsn[len("postgresql://"):],
           "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    yield postgres_container


@pytest.fixture
async def pg(migrated_db: dict[str, str]) -> Any:
    from libs.database_connector.postgres_connector import client

    client.set_url("postgresql+asyncpg://" + migrated_db["dsn"][len("postgresql://"):])
    await client.open()
    yield client
    await client.close()


class Broker:
    """A publish callable that records what was sent and can be told to fail."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.down = False
        self.poison_topic: str | None = None

    async def publish(self, topic: str, value: dict[str, Any], key: str | None) -> None:
        if self.down or topic == self.poison_topic:
            raise RuntimeError("broker unavailable")
        self.sent.append((topic, value, key))


def _rows(dsn: str) -> list[tuple[Any, ...]]:
    SessionLocal = _sync_session_factory(dsn)
    with SessionLocal() as session:
        rows = session.scalars(select(OutboxEventORM).order_by(OutboxEventORM.id)).all()
        return [(row.id, row.topic, row.published_at is not None, row.attempts, row.dead_at is not None) for row in rows]


def _sync_session_factory(dsn: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_engine(dsn.replace("postgresql://", "postgresql+psycopg2://", 1), future=True),
        expire_on_commit=False,
        future=True,
    )


async def _enqueue(pg: Any, topic: str, n: int, *, producer: str = "p1") -> None:
    from libs.shared import outbox

    async with pg.get_platform_client() as session:
        await outbox.enqueue(session, producer=producer, topic=topic, value={"n": n}, key=f"k{n}")
        await session.commit()


async def test_an_event_waits_out_a_broker_outage_and_keeps_its_order(pg: Any, migrated_db: dict[str, str]) -> None:
    from libs.shared import outbox

    broker = Broker()
    for n in range(3):
        await _enqueue(pg, "t", n)

    broker.down = True
    assert await outbox.drain(pg.get_platform_client, broker.publish, producer="p1") == 0
    assert broker.sent == []
    assert [r[2] for r in _rows(migrated_db["dsn"])] == [False, False, False]  # nothing lost, nothing marked sent

    broker.down = False
    assert await outbox.drain(pg.get_platform_client, broker.publish, producer="p1") == 3
    assert [v["n"] for _, v, _ in broker.sent] == [0, 1, 2]
    assert [r[2] for r in _rows(migrated_db["dsn"])] == [True, True, True]
    assert await outbox.drain(pg.get_platform_client, broker.publish, producer="p1") == 0  # nothing republished


async def test_a_relay_only_drains_its_own_producers_rows(pg: Any) -> None:
    from libs.shared import outbox

    broker = Broker()
    await _enqueue(pg, "mine", 1, producer="p1")
    await _enqueue(pg, "theirs", 2, producer="p2")
    await outbox.drain(pg.get_platform_client, broker.publish, producer="p1")
    assert [t for t, _, _ in broker.sent] == ["mine"]


async def test_two_relays_never_publish_the_same_row(pg: Any) -> None:
    from libs.shared import outbox

    broker = Broker()
    for n in range(40):
        await _enqueue(pg, "t", n)

    async def slow_publish(topic: str, value: dict[str, Any], key: str | None) -> None:
        await asyncio.sleep(0.01)  # widen the window in which both relays hold a batch
        await broker.publish(topic, value, key)

    await asyncio.gather(*(
        outbox.drain(pg.get_platform_client, slow_publish, producer="p1", batch=10) for _ in range(4)
    ))
    await outbox.drain(pg.get_platform_client, slow_publish, producer="p1", batch=50)
    numbers = sorted(v["n"] for _, v, _ in broker.sent)
    assert numbers == list(range(40)), "each event exactly once"


async def test_a_poison_event_is_parked_and_does_not_block_the_queue(pg: Any, migrated_db: dict[str, str]) -> None:
    from libs.shared import outbox

    broker = Broker()
    broker.poison_topic = "poison"
    await _enqueue(pg, "poison", 0)
    await _enqueue(pg, "fine", 1)

    for _ in range(outbox.MAX_ATTEMPTS):
        await outbox.drain(pg.get_platform_client, broker.publish, producer="p1")
    assert broker.sent == [], "the poison row blocked the row behind it until it was parked"

    await outbox.drain(pg.get_platform_client, broker.publish, producer="p1")
    assert [t for t, _, _ in broker.sent] == ["fine"]
    poison = _rows(migrated_db["dsn"])[0]
    assert poison[2] is False and poison[4] is True  # never published, parked


async def test_an_event_enqueued_in_a_rolled_back_transaction_does_not_exist(pg: Any, migrated_db: dict[str, str]) -> None:
    from libs.shared import outbox

    async with pg.get_platform_client() as session:
        await outbox.enqueue(session, producer="p1", topic="t", value={"n": 1})
        await session.rollback()
    assert _rows(migrated_db["dsn"]) == []


def test_the_synchronous_orm_flavour_has_the_same_claim_and_failure_rules(migrated_db: dict[str, str]) -> None:
    """The crawler worker uses a sync ORM session; rollback hides the event and
    drain_sync_orm keeps a failed row pending."""
    from libs.shared import outbox

    sent: list[str] = []
    fail = {"on": True}

    def publish(topic: str, value: dict[str, Any], key: str | None) -> None:
        if fail["on"]:
            raise RuntimeError("kafka down")
        sent.append(topic)

    SessionLocal = _sync_session_factory(migrated_db["dsn"])
    with SessionLocal() as session:
        outbox.enqueue_sync_orm(session, producer="web-crawler", topic="kb.promote.silver", value={"data_id": "d1"}, key="d1")
        session.rollback()  # the caller's transaction failed: the event must not exist
        assert _rows(migrated_db["dsn"]) == []

    with SessionLocal() as session:
        outbox.enqueue_sync_orm(session, producer="web-crawler", topic="kb.promote.silver", value={"data_id": "d1"}, key="d1")
        session.commit()

    assert outbox.drain_sync_orm(SessionLocal, publish, producer="web-crawler") == 0
    assert [r[2] for r in _rows(migrated_db["dsn"])] == [False]
    fail["on"] = False
    assert outbox.drain_sync_orm(SessionLocal, publish, producer="web-crawler") == 1
    assert sent == ["kb.promote.silver"]
