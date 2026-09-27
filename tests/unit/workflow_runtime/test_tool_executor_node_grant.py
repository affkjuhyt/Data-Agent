"""tool_executor_node wiring of the §1.9 grant check — rejected calls must
surface as a normal tool_errors/_tool_traces entry (so the agent/judge loop
can see and react to it), not raise uncaught or silently vanish, and must
never reach the real handler in TOOL_REGISTRY.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
async def test_not_granted_sandbox_tool_never_reaches_handler(monkeypatch):
    import nodes.sandbox.tool_executor.node as node_mod

    fake_handler = AsyncMock(return_value=({"id": "x"}, None))
    monkeypatch.setitem(node_mod.TOOL_REGISTRY, "execute_python", fake_handler)

    state = {
        "config": {"allowed_tools": ["execute_python"], "tool_grant": {"preset": "sql_readonly"}},
        "tool_calls": [{"type": "tool", "tool_name": "execute_python", "query": "print(1)", "arguments": {}}],
    }

    result = await node_mod.tool_executor_node(state)

    fake_handler.assert_not_called()
    assert result["tool_results"] == []
    assert len(result["tool_errors"]) == 1
    assert result["tool_errors"][0]["tool_name"] == "execute_python"
    assert "not granted" in result["tool_errors"][0]["error"]
    assert result["_tool_traces"][0]["status"] == "error"


@pytest.mark.unit
async def test_granted_sandbox_tool_reaches_handler(monkeypatch):
    import nodes.sandbox.tool_executor.node as node_mod

    fake_handler = AsyncMock(return_value=({"id": "x", "content": "ok", "source": "tool/execute_python", "metadata": {}}, None))
    monkeypatch.setitem(node_mod.TOOL_REGISTRY, "execute_python", fake_handler)

    state = {
        "config": {"allowed_tools": ["execute_python"], "tool_grant": {"preset": "python_analysis"}},
        "tool_calls": [{"type": "tool", "tool_name": "execute_python", "query": "print(1)", "arguments": {}}],
    }

    result = await node_mod.tool_executor_node(state)

    fake_handler.assert_called_once()
    assert len(result["tool_results"]) == 1
    assert result["tool_errors"] == []


@pytest.mark.unit
async def test_non_sandbox_tools_unaffected_by_grant_config(monkeypatch):
    """A restrictive tool_grant preset must not touch non-sandbox tools —
    bash stays governed only by allowed_tools, same as before this feature."""
    import nodes.sandbox.tool_executor.node as node_mod

    fake_handler = AsyncMock(return_value=({"id": "x"}, None))
    monkeypatch.setitem(node_mod.TOOL_REGISTRY, "bash", fake_handler)

    state = {
        "config": {"allowed_tools": ["bash"], "tool_grant": {"preset": "sql_readonly"}},
        "tool_calls": [{"type": "tool", "tool_name": "bash", "query": "ls", "arguments": {}}],
    }

    result = await node_mod.tool_executor_node(state)

    fake_handler.assert_called_once()
    assert result["tool_errors"] == []
