"""delegate_to_agent — the agent-to-agent hop is in-process, not HTTP.

The handler used to POST to ``/api/conversations/run-sync``, an endpoint that
never existed: every delegation 404'd. It now calls
``orchestrator.executor.agent_client.call_agent`` directly (the tool executor
runs inside workflow_runtime, same process as the orchestrator).

Pure-mock tests. Two things are kept out of the import path on purpose:

* ``agent_client`` is stubbed into sys.modules before the handler's lazy
  import runs — the real orchestrator package pulls the planner/observability
  stack at import time;
* the ``tool_executor`` package ``__init__`` is bypassed (it imports node.py →
  handlers/data.py → litellm, and in this suite litellm may already sit
  half-imported in sys.modules after an unrelated test's import failure). The
  handler module and its ``_utils`` dependency are loaded for real through a
  path-only package stub.
"""
from __future__ import annotations

import pathlib
import sys
import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

_PKG = "services.workflow_runtime.application.nodes.sandbox.tool_executor"
_PKG_DIR = (
    pathlib.Path(__file__).resolve().parents[3]
    / "backend" / "services" / "workflow_runtime" / "application" / "nodes" / "sandbox" / "tool_executor"
)

CFG = {
    "tenant_id": "a0000000-0000-0000-0000-000000000001",
    "user_ref": "nguyen.van.a",
    "user_roles": ["business-operator"],
    "auth_source": "gateway",
    "session_id": "sess-1",
    "agent_id": "agent-parent",
}


@pytest.fixture
def handler(monkeypatch):
    """The real ``_delegate_to_agent_handler``, imported without running the
    tool_executor package ``__init__`` (see module docstring)."""
    stub_pkg = types.ModuleType(_PKG)
    stub_pkg.__path__ = [str(_PKG_DIR)]
    monkeypatch.setitem(sys.modules, _PKG, stub_pkg)
    for name in (f"{_PKG}._utils", f"{_PKG}.handlers", f"{_PKG}.handlers.agent"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.agent import (
        _delegate_to_agent_handler,
    )
    return _delegate_to_agent_handler


def _stub_agent_client(monkeypatch, call_agent: Any) -> None:
    """Install a fake orchestrator.executor.agent_client before the handler's
    lazy import resolves it."""
    mod = types.ModuleType("services.workflow_runtime.application.orchestrator.executor.agent_client")
    mod.call_agent = call_agent
    pkg = types.ModuleType("services.workflow_runtime.application.orchestrator.executor")
    pkg.agent_client = mod
    monkeypatch.setitem(sys.modules, "services.workflow_runtime.application.orchestrator.executor", pkg)
    monkeypatch.setitem(sys.modules, "services.workflow_runtime.application.orchestrator.executor.agent_client", mod)


class TestDelegateToAgent:
    async def test_invokes_call_agent_and_returns_its_text(self, handler, monkeypatch):
        call_agent = AsyncMock(return_value=("Doanh thu Q3 là 12 tỷ.", "conv-42", []))
        _stub_agent_client(monkeypatch, call_agent)

        result, error = await handler(
            {"arguments": {"target_agent": "agent-sales", "prompt": "Doanh thu Q3?"}},
            CFG,
        )

        assert error is None
        assert result is not None
        assert result["content"] == "Doanh thu Q3 là 12 tỷ."
        assert result["source"] == "tool/delegate/agent-sales"
        assert result["id"].startswith("delegate_")

    async def test_caller_identity_travels_verbatim_and_orchestrated_is_set(self, handler, monkeypatch):
        """Roles pass through as given — the sub-agent's kb_search/RBAC checks
        must run as the REAL user. orchestrated=True stops the sub-agent from
        generating its own follow-up suggestions inside a delegated turn."""
        call_agent = AsyncMock(return_value=("ok", None, []))
        _stub_agent_client(monkeypatch, call_agent)

        await handler({"arguments": {"target_agent": "agent-x", "prompt": "hi"}}, CFG)

        call_agent.assert_awaited_once()
        args, kwargs = call_agent.await_args
        assert args == ("agent-x", "hi")
        assert kwargs["user_id"] == "nguyen.van.a"
        assert kwargs["tenant_id"] == CFG["tenant_id"]
        assert kwargs["user_roles"] == ["business-operator"]
        assert kwargs["orchestrated"] is True

    async def test_missing_target_or_prompt_is_an_error_not_a_call(self, handler, monkeypatch):
        call_agent = AsyncMock()
        _stub_agent_client(monkeypatch, call_agent)

        result, error = await handler({"arguments": {"prompt": "hi"}}, CFG)
        assert result is None and error is not None
        assert error["tool_name"] == "delegate_to_agent"

        result, error = await handler({"arguments": {"target_agent": "a"}}, CFG)
        assert result is None and error is not None
        call_agent.assert_not_awaited()

    async def test_a_failing_sub_agent_becomes_a_tool_error_string(self, handler, monkeypatch):
        """The handler contract: (None, {tool_name, error}) — never a raised
        exception up into the executor's gather."""
        call_agent = AsyncMock(side_effect=RuntimeError("agent turn timed out"))
        _stub_agent_client(monkeypatch, call_agent)

        result, error = await handler(
            {"arguments": {"target_agent": "agent-x", "prompt": "hi"}},
            CFG,
        )

        assert result is None
        assert error == {"tool_name": "delegate_to_agent", "error": "agent turn timed out"}

    async def test_prompt_falls_back_to_the_call_query(self, handler, monkeypatch):
        call_agent = AsyncMock(return_value=("ans", None, []))
        _stub_agent_client(monkeypatch, call_agent)

        result, error = await handler(
            {"arguments": {"target_agent": "agent-x"}, "query": "câu hỏi gốc"},
            CFG,
        )

        assert error is None and result is not None
        assert call_agent.await_args.args == ("agent-x", "câu hỏi gốc")
