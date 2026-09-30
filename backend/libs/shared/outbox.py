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
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

# After this many failed publishes a row is parked (dead_at) so it cannot block the
# events queued behind it (e.g. a payload the broker rejects for size, forever).
MAX_ATTEMPTS = 20

_INSERT = (
    'INSERT INTO "OutboxEvents" (producer, topic, event_key, payload) '
    "VALUES (:producer, :topic, :key, CAST(:payload AS jsonb))"
)
_CLAIM = (
    'SELECT id, topic, event_key, payload FROM "OutboxEvents" '
    "WHERE producer = :producer AND published_at IS NULL AND dead_at IS NULL "
    "ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED"
)
_SENT = 'UPDATE "OutboxEvents" SET published_at = now(), attempts = attempts + 1, last_error = NULL WHERE id = :id'
_FAILED = (
    'UPDATE "OutboxEvents" SET attempts = attempts + 1, last_error = :err, '
    "dead_at = CASE WHEN attempts + 1 >= :max THEN now() ELSE NULL END WHERE id = :id"
)

PublishFn = Callable[[str, dict[str, Any], str | None], Awaitable[None]]


async def enqueue(
    session: Any, *, producer: str, topic: str, value: dict[str, Any], key: str | None = None
) -> None:
    """Add an event inside the caller's open transaction (commit is the caller's)."""
    from sqlalchemy import text  # lazy: the synchronous crawler image has no SQLAlchemy

    await session.execute(
        text(_INSERT),
        {"producer": producer, "topic": topic, "key": key, "payload": json.dumps(value, default=str)},
    )


def enqueue_sync(cur: Any, *, producer: str, topic: str, value: dict[str, Any], key: str | None = None) -> None:
    """psycopg2 flavour of `enqueue`, for the synchronous crawler worker."""
    cur.execute(
        'INSERT INTO "OutboxEvents" (producer, topic, event_key, payload) VALUES (%s, %s, %s, %s::jsonb)',
        (producer, topic, key, json.dumps(value, default=str)),
    )


async def drain(
    session_factory: Callable[[], Any], publish: PublishFn, *, producer: str, batch: int = 50
) -> int:
    """Publish up to `batch` pending events of `producer`, oldest first. Returns how many went out.

    Rows are claimed with FOR UPDATE SKIP LOCKED so several replicas can relay at once
    without publishing the same row twice. The first failure ends the batch (order within
    a producer is preserved) and is recorded on the row.
    """
    from sqlalchemy import text  # lazy, see enqueue

    sent = 0
    async with session_factory() as session:
        rows = (await session.execute(text(_CLAIM), {"producer": producer, "n": batch})).all()
        for row_id, topic, key, payload in rows:
            try:
                await publish(topic, payload, key)
            except Exception as exc:  # noqa: BLE001 — recorded on the row, retried next pass
                log.warning("outbox publish failed producer=%s id=%s topic=%s: %s", producer, row_id, topic, exc)
                await session.execute(text(_FAILED), {"id": row_id, "err": str(exc)[:500], "max": MAX_ATTEMPTS})
                break
            await session.execute(text(_SENT), {"id": row_id})
            sent += 1
        await session.commit()
    return sent


def drain_sync(conn: Any, publish: Callable[[str, dict[str, Any], str | None], None], *, producer: str, batch: int = 50) -> int:
    """psycopg2 flavour of `drain` (same claim/ordering/failure rules)."""
    sent = 0
    with conn.cursor() as cur:
        cur.execute(
            'SELECT id, topic, event_key, payload FROM "OutboxEvents" '
            "WHERE producer = %s AND published_at IS NULL AND dead_at IS NULL "
            "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
            (producer, batch),
        )
        for row_id, topic, key, payload in cur.fetchall():
            try:
                publish(topic, payload, key)
            except Exception as exc:  # noqa: BLE001
                log.warning("outbox publish failed producer=%s id=%s topic=%s: %s", producer, row_id, topic, exc)
                cur.execute(
                    'UPDATE "OutboxEvents" SET attempts = attempts + 1, last_error = %s, '
                    "dead_at = CASE WHEN attempts + 1 >= %s THEN now() ELSE NULL END WHERE id = %s",
                    (str(exc)[:500], MAX_ATTEMPTS, row_id),
                )
                break
            cur.execute(
                'UPDATE "OutboxEvents" SET published_at = now(), attempts = attempts + 1, last_error = NULL WHERE id = %s',
                (row_id,),
            )
            sent += 1
    conn.commit()
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
        except Exception:  # noqa: BLE001 — Postgres hiccup; try again next tick
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
