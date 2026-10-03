"""A turn whose stream is closed before it has an answer is not saved.

The orchestrator starts the agent on the question while input_refine reads it,
and drops that run when input_refine rewrites the question. The dropped run
still saved its turn: the question with an empty reply, in the history beside
the turn that superseded it (dev cluster, 02/10/2026).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.workflow_runtime.application.runtime_services import conversation_runner as runner
from services.workflow_runtime.domain.schemas import RunRequest

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
CALLER = {"user_id": "user-1", "tenant_id": TENANT, "roles": ["viewer"]}


class _Graph:
    """Starts a node, then either finishes or waits until it is cancelled."""

    def __init__(self, finishes: bool) -> None:
        self.finishes = finishes

    async def astream_events(self, *_: Any, **__: Any):
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "planner"}, "data": {}}
        if not self.finishes:
            await asyncio.Event().wait()

    def get_state(self, _config: Any) -> Any:
        raise RuntimeError("no checkpointer")


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Names of the coroutines the runner hands to _fire_and_forget."""
    names: list[str] = []

    def _record(coro: Any) -> None:
        names.append(coro.cr_code.co_name)
        coro.close()

    monkeypatch.setattr(runner, "_fire_and_forget", _record)
    monkeypatch.setattr(
        runner, "load_agent_config", AsyncMock(return_value={"agent_id": "agent-1"})
    )
    monkeypatch.setattr(runner, "validate_agent_tenant", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "create_conversation", AsyncMock(return_value="conv-1"))
    monkeypatch.setattr(runner, "get_langchain_callback_handler", lambda **_: None)
    return names


async def _stream(monkeypatch: pytest.MonkeyPatch, graph: _Graph) -> Any:
    monkeypatch.setattr(runner, "_default_graph", graph)
    response = await runner.run_conversation_stream(
        RunRequest(query="giá vàng SJC", agent_id="agent-1"),
        SimpleNamespace(headers={}),  # type: ignore[arg-type]
        CALLER,
    )
    return response.body_iterator


async def test_a_stream_closed_before_an_answer_saves_nothing(monkeypatch, scheduled) -> None:
    body = await _stream(monkeypatch, _Graph(finishes=False))
    consumer = asyncio.create_task(body.__anext__())
    await asyncio.sleep(0.05)

    consumer.cancel()  # what stopping the speculative agent run does
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert "_persist_turn" not in scheduled


async def test_a_finished_turn_is_saved(monkeypatch, scheduled) -> None:
    body = await _stream(monkeypatch, _Graph(finishes=True))
    [frame async for frame in body]

    assert "_persist_turn" in scheduled
