"""A turn of a memory-enabled agent is sent for extraction.

The caller already checks the agent's memory_enabled (the Memory toggle). The
function used to also require a "MemoryPolicy" row with action 'add'; nothing
ever wrote that table, so no turn was ever extracted and the memory tables
stayed empty. Now it only records the request in the outbox.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from services.workflow_runtime.infrastructure import memory_middleware

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
AGENT = "a0000000-0000-0000-0000-000000000030"


class _Session:
    def __init__(self) -> None:
        self.committed = False

    async def execute(self, statement: Any) -> Any:
        raise AssertionError("no lookup may gate the extraction request")

    async def commit(self) -> None:
        self.committed = True


class _Client:
    def __init__(self) -> None:
        self.session = _Session()

    def is_connected(self) -> bool:
        return True

    @asynccontextmanager
    async def get_client(self, **_: Any):
        yield self.session


def test_the_turn_is_queued_for_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    enqueued: list[dict[str, Any]] = []

    async def fake_enqueue(session: Any, **kwargs: Any) -> None:
        enqueued.append(kwargs)

    monkeypatch.setattr(memory_middleware, "client", client)
    monkeypatch.setattr(memory_middleware.outbox, "enqueue", fake_enqueue)

    asyncio.run(
        memory_middleware.request_memory_extraction(
            AGENT, TENANT, "kc-user-42", conversation_id="conv-1", message_id="msg-1"
        )
    )

    assert client.session.committed
    [request] = enqueued
    assert request["topic"] == "memory.extraction.requested"
    assert request["key"] == "conv-1"
    assert request["value"] == {
        "tenant_id": TENANT,
        "agent_id": AGENT,
        "user_ref": "kc-user-42",
        "conversation_id": "conv-1",
        "message_id": "msg-1",
    }


def test_nothing_is_queued_without_a_saved_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    enqueued: list[Any] = []

    async def fake_enqueue(session: Any, **kwargs: Any) -> None:
        enqueued.append(kwargs)

    monkeypatch.setattr(memory_middleware, "client", client)
    monkeypatch.setattr(memory_middleware.outbox, "enqueue", fake_enqueue)

    asyncio.run(
        memory_middleware.request_memory_extraction(
            AGENT, TENANT, "kc-user-42", conversation_id="conv-1", message_id=None
        )
    )

    assert enqueued == []
