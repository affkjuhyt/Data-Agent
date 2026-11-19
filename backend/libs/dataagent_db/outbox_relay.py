"""What every outbox relay does with a claimed event, handled or not (PL-02).

Until Debezium publishes the outboxes (ADR-06), each worker polls the ones it consumes. A handler
that raised used to roll its whole batch back: the batch came back on the next claim, failing event
first, forever, and the events behind it waited. Now each event is settled on its own, in the
claim's transaction:

- handled: published (`published_at`), as before;
- raised: `attempts` + 1, `last_error`, and not claimed again before `next_attempt_at`
  (BACKOFF: 5, 15, 60, 180 minutes);
- raised for the DEAD_AFTER-th time: `dead_at`, out of every claim, and an `outbox.dead_letter`
  event on integration.outbox, which worker-agent turns into a notice to the organisation's admins
  (worker_agent/ops/dead_letter.py). The audit database has no integration.outbox: there the dead
  event is logged and counted only.

`Claimed` is the list a source's `claim()` yields: the loop calls `fail(event_id, error)` for an
event whose handler raised and goes on with the next; leaving the claim settles every event. A
source without `fail` (the tests' fakes) keeps the old meaning: an error leaves the whole batch.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.base import uuid7

logger = logging.getLogger(__name__)

BACKOFF_MINUTES = (5, 15, 60, 180)
DEAD_AFTER = 5
# What a claim may take: neither published nor dead, and due.
DUE = (
    "published_at IS NULL AND dead_at IS NULL "
    "AND (next_attempt_at IS NULL OR next_attempt_at <= now())"
)
# The error a row keeps: enough to read the cause, never a whole traceback in a table.
ERROR_CHARS = 2_000

T = TypeVar("T")


class Claimed(list[T]):
    """The events of one claim, and which of them failed."""

    def __init__(self, events: Iterable[T]) -> None:
        super().__init__(events)
        self.failures: dict[uuid.UUID, str] = {}

    def fail(self, event_id: uuid.UUID, error: BaseException | str) -> None:
        message = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        self.failures[event_id] = message[:ERROR_CHARS]


def backoff_minutes(attempts: int) -> int:
    """The wait before try `attempts + 1`, `attempts` being the failures so far (1-based)."""
    return BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]


async def settle(
    session: AsyncSession,
    schema: str,
    event_ids: Iterable[uuid.UUID],
    failures: dict[uuid.UUID, str],
    *,
    dead_letter: bool = True,
) -> list[dict[str, Any]]:
    """Publish the handled events and record the failed ones; returns the events that died.

    `schema` is ours (a source's constant), never input: it is formatted, the rest is bound.
    """
    handled = [i for i in event_ids if i not in failures]
    if handled:
        await session.execute(
            text(f"UPDATE {schema}.outbox SET published_at = now() WHERE id = ANY(:ids)"),
            {"ids": handled},
        )
    died: list[dict[str, Any]] = []
    for event_id, error in failures.items():
        row = (
            await session.execute(
                text(
                    f"UPDATE {schema}.outbox SET attempts = attempts + 1, last_error = :error, "
                    "next_attempt_at = now() + make_interval(mins => CASE attempts + 1 "
                    + " ".join(f"WHEN {n + 1} THEN {m}" for n, m in enumerate(BACKOFF_MINUTES))
                    + f" ELSE {BACKOFF_MINUTES[-1]} END), "
                    f"dead_at = CASE WHEN attempts + 1 >= {DEAD_AFTER} THEN now() END "
                    "WHERE id = :id "
                    "RETURNING org_id, event_type, aggregate_type, aggregate_id, attempts, dead_at"
                ),
                {"id": event_id, "error": error},
            )
        ).one_or_none()
        if row is None:
            continue
        if row.dead_at is None:
            logger.warning(
                "outbox | %s.%s %s failed (try %s), retried later: %s",
                schema,
                row.event_type,
                event_id,
                row.attempts,
                error,
            )
            continue
        logger.error(
            "outbox | %s.%s %s is dead after %s tries: %s",
            schema,
            row.event_type,
            event_id,
            row.attempts,
            error,
        )
        dead = {
            "schema": schema,
            "event_id": str(event_id),
            "event_type": row.event_type,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": str(row.aggregate_id),
            "attempts": row.attempts,
            "last_error": error,
        }
        died.append(dead)
        if dead_letter:
            await session.execute(
                text(
                    "INSERT INTO integration.outbox (id, org_id, aggregate_type, aggregate_id,"
                    " event_type, payload) VALUES (:id, :org_id, 'outbox_event', :event_id,"
                    " 'outbox.dead_letter', CAST(:payload AS jsonb))"
                ),
                {
                    "id": uuid7(),
                    "org_id": row.org_id,
                    "event_id": event_id,
                    "payload": json.dumps(dead),
                },
            )
    return died
