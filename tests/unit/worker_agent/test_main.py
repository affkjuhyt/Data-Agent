"""The worker loop acknowledges a batch only when every handler returned."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from services.worker_agent.events import Event
from services.worker_agent.main import run_once

pytestmark = [pytest.mark.unit]


def _event(kind: str = "question.received") -> Event:
    return Event(uuid.uuid4(), uuid.uuid4(), kind, "question", uuid.uuid4(), {})


class FakeSource:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.acked: list[Event] = []

    @asynccontextmanager
    async def claim(self, batch: int) -> AsyncIterator[list[Event]]:
        taken = self.events[:batch]
        yield taken
        self.acked += taken
        self.events = self.events[batch:]


async def test_handles_and_acknowledges_a_batch() -> None:
    events = [_event(), _event()]
    src, seen = FakeSource(events), []

    async def handle(e: Event) -> None:
        seen.append(e)

    assert await run_once(src, {"question.received": handle}, batch=10) == 2
    assert seen == events and src.acked == events


async def test_a_failing_handler_leaves_the_batch_unacknowledged() -> None:
    src = FakeSource([_event(), _event()])

    async def boom(e: Event) -> None:
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        await run_once(src, {"question.received": boom})
    assert src.acked == [] and len(src.events) == 2


async def test_empty_claim_handles_nothing() -> None:
    assert await run_once(FakeSource([]), {}) == 0
