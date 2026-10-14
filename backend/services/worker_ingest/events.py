"""Transactional outbox source used by worker-ingest until Kafka Connect is enabled."""

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
    def claim(self, batch: int) -> Any: ...


class OutboxTableSource:
    def __init__(self, event_types: frozenset[str]) -> None:
        self._types = sorted(event_types)

    @asynccontextmanager
    async def claim(self, batch: int) -> AsyncIterator[list[Event]]:
        async with unscoped_session("svc_outbox_relay") as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, org_id, event_type, aggregate_type, aggregate_id, payload "
                        "FROM ingest.outbox WHERE published_at IS NULL "
                        "AND event_type = ANY(:types) ORDER BY created_at LIMIT :batch "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    {"types": self._types, "batch": batch},
                )
            ).all()
            events = [
                Event(
                    r.id,
                    r.org_id,
                    r.event_type,
                    r.aggregate_type,
                    r.aggregate_id,
                    dict(r.payload or {}),
                )
                for r in rows
            ]
            yield events
            if events:
                await session.execute(
                    text("UPDATE ingest.outbox SET published_at = now() WHERE id = ANY(:ids)"),
                    {"ids": [event.event_id for event in events]},
                )
