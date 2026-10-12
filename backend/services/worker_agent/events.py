"""How events reach the worker.

ADR-06/32: each module writes its events to <schema>.outbox in the transaction
that changes the data; Debezium publishes them to Kafka (topic = event_type,
event id = outbox.id) and consumers dedupe on that id. The dev cluster has no
Kafka Connect yet, so `OutboxTableSource` reads qa.outbox directly: it claims
unpublished rows of the event types the worker handles, FOR UPDATE SKIP LOCKED
so several replicas never take the same row, and stamps published_at in the
same transaction once the handler has committed its own work.

The handlers see only `Event`, so swapping this source for a Kafka consumer is
local to this file. Claiming needs a role that sees every organisation's
outbox rows: svc_outbox_relay may read every outbox row and stamp published_at, nothing else
(its policies are in the DDL); the module roles are scoped to one org by RLS.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from libs.dataagent_db import unscoped_session


@dataclass(frozen=True)
class Event:
    event_id: uuid.UUID
    org_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]


Handler = Callable[[Event], Awaitable[None]]


class EventSource(Protocol):
    def claim(self, batch: int) -> Any:
        """An async context manager yielding the claimed events; leaving it without an error
        acknowledges every one of them, an error leaves them for the next claim."""
        ...


class OutboxTableSource:
    def __init__(
        self, *, schema: str, event_types: frozenset[str], role: str = "svc_outbox_relay"
    ) -> None:
        self._schema = schema
        self._types = sorted(event_types)
        self._role = role

    @asynccontextmanager
    async def claim(self, batch: int) -> AsyncIterator[list[Event]]:
        # The schema name is ours, never input: it is formatted, the rest is bound.
        claim_sql = text(
            f"SELECT id, org_id, event_type, aggregate_type, aggregate_id, payload "
            f"FROM {self._schema}.outbox "
            f"WHERE published_at IS NULL AND event_type = ANY(:types) "
            f"ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED"
        )
        ack_sql = text(
            f"UPDATE {self._schema}.outbox SET published_at = now() WHERE id = ANY(:ids)"
        )
        async with unscoped_session(self._role) as session:
            rows = (await session.execute(claim_sql, {"types": self._types, "batch": batch})).all()
            events = [
                Event(
                    event_id=r.id,
                    org_id=r.org_id,
                    event_type=r.event_type,
                    aggregate_type=r.aggregate_type,
                    aggregate_id=r.aggregate_id,
                    payload=dict(r.payload or {}),
                )
                for r in rows
            ]
            yield events
            if events:
                await session.execute(ack_sql, {"ids": [e.event_id for e in events]})
