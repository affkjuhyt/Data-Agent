from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

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


class DocumentOutboxSource:
    def __init__(self, event_types: frozenset[str]) -> None:
        self._types = sorted(event_types)

    @asynccontextmanager
    async def claim(self, batch: int) -> AsyncIterator[list[Event]]:
        async with unscoped_session("svc_outbox_relay") as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, org_id, event_type, aggregate_type, aggregate_id, payload "
                        "FROM document.outbox WHERE event_type = ANY(:types) "
                        "AND published_at IS NULL "
                        "ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED"
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
                    text("UPDATE document.outbox SET published_at = now() WHERE id = ANY(:ids)"),
                    {"ids": [event.event_id for event in events]},
                )
