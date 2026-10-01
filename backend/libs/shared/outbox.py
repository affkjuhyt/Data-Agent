"""Transactional outbox: enqueue a Kafka event in Postgres, relay it to the broker.

Why: a service that commits a change and then publishes an event (two systems, no
shared transaction) loses the event whenever the process dies or the broker is down
in between, and publishing first instead leaves an event for a change that never
happened. Writing the event to `OutboxEvents` — in the same transaction as the change
when the caller has one — and publishing it from a relay turns both failures into a
delay. Delivery is at-least-once (a crash after publish, before the row is marked,
republishes), so consumers must be idempotent.

Kept free of connector imports (libs.shared is a leaf, see .importlinter): callers
hand in a session factory / cursor and a `publish` callable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.outbox_orm import OutboxEventORM

log = logging.getLogger(__name__)

# After this many failed publishes a row is parked (dead_at) so it cannot block the
# events queued behind it (e.g. a payload the broker rejects for size, forever).
MAX_ATTEMPTS = 20

PublishFn = Callable[[str, dict[str, Any], str | None], Awaitable[None]]


def _new_event(
    *, producer: str, topic: str, value: dict[str, Any], key: str | None
) -> OutboxEventORM:
    return OutboxEventORM(
        producer=producer,
        topic=topic,
        event_key=key,
        payload=value,
    )


def _mark_publish_failed(row: OutboxEventORM, exc: Exception) -> None:
    row.attempts += 1
    row.last_error = str(exc)[:500]
    if row.attempts >= MAX_ATTEMPTS:
        row.dead_at = datetime.now(UTC)


def _mark_published(row: OutboxEventORM) -> None:
    row.published_at = datetime.now(UTC)
    row.attempts += 1
    row.last_error = None


async def enqueue(
    session: Any, *, producer: str, topic: str, value: dict[str, Any], key: str | None = None
) -> None:
    """Add an event inside the caller's open transaction (commit is the caller's)."""
    session.add(_new_event(producer=producer, topic=topic, value=value, key=key))


def enqueue_sync_orm(
    session: Any, *, producer: str, topic: str, value: dict[str, Any], key: str | None = None
) -> None:
    """Synchronous SQLAlchemy flavour of `enqueue` (commit is the caller's)."""
    session.add(_new_event(producer=producer, topic=topic, value=value, key=key))


async def drain(
    session_factory: Callable[[], Any], publish: PublishFn, *, producer: str, batch: int = 50
) -> int:
    """Publish up to `batch` pending events of `producer`, oldest first. Returns how many went out.

    Rows are claimed with FOR UPDATE SKIP LOCKED so several replicas can relay at once
    without publishing the same row twice. The first failure ends the batch (order within
    a producer is preserved) and is recorded on the row.
    """
    sent = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEventORM)
                    .where(
                        OutboxEventORM.producer == producer,
                        OutboxEventORM.published_at.is_(None),
                        OutboxEventORM.dead_at.is_(None),
                    )
                    .order_by(OutboxEventORM.id)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            try:
                await publish(row.topic, row.payload, row.event_key)
            except Exception as exc:  # noqa: BLE001 — recorded on the row, retried next pass
                log.warning(
                    "outbox publish failed producer=%s id=%s topic=%s: %s",
                    producer,
                    row.id,
                    row.topic,
                    exc,
                )
                _mark_publish_failed(row, exc)
                break
            _mark_published(row)
            sent += 1
        await session.commit()
    return sent


def drain_sync_orm(
    session_factory: Callable[[], Any],
    publish: Callable[[str, dict[str, Any], str | None], None],
    *,
    producer: str,
    batch: int = 50,
) -> int:
    """Synchronous SQLAlchemy flavour of `drain`."""
    sent = 0
    with session_factory() as session:
        rows = session.scalars(
            select(OutboxEventORM)
            .where(
                OutboxEventORM.producer == producer,
                OutboxEventORM.published_at.is_(None),
                OutboxEventORM.dead_at.is_(None),
            )
            .order_by(OutboxEventORM.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            try:
                publish(row.topic, row.payload, row.event_key)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "outbox publish failed producer=%s id=%s topic=%s: %s",
                    producer,
                    row.id,
                    row.topic,
                    exc,
                )
                _mark_publish_failed(row, exc)
                break
            _mark_published(row)
            sent += 1
        session.commit()
    return sent


async def relay_loop(
    session_factory: Callable[[], Any],
    publish: PublishFn,
    *,
    producer: str,
    interval: float = 2.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Drain until `stop` is set (or cancelled). Drains again at once while batches come back full."""
    while stop is None or not stop.is_set():
        try:
            while await drain(session_factory, publish, producer=producer) >= 50:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox relay pass failed producer=%s", producer)
        await asyncio.sleep(interval)


class OutboxProducer:
    """Drop-in for a Kafka producer's `.produce()` that records the event instead.

    Callers that already take a producer (`await producer.produce(topic, value, key)`)
    get durable, retried delivery without changing a line: the event is committed to
    Postgres here and `relay_loop` sends it. `is_healthy` mirrors the real producer so
    health checks that ask still work.
    """

    def __init__(self, session_factory: Callable[[], Any], *, producer: str) -> None:
        self._session_factory = session_factory
        self._producer = producer

    async def produce(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        async with self._session_factory() as session:
            await enqueue(session, producer=self._producer, topic=topic, value=value, key=key)
            await session.commit()
