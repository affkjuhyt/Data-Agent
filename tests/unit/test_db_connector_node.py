"""db_connector_node (services/workflow-runtime/nodes/sandbox/db_connector.py)
— very thin: all real logic (schema introspection, DB selection, parallel
SQL execution) lives in _text_to_sql_handler (see
test_text_to_sql_handler.py). This node just extracts the query from
tool_calls and maps the handler's (results, errors) tuple into
state["db_results"]/state["db_error"]."""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


async def test_maps_successful_results(monkeypatch):
    import nodes.sandbox.db_connector as mod
    fake_handler = AsyncMock(return_value=([{"id": "sql_1", "content": "[]"}], []))
    monkeypatch.setattr(mod, "_text_to_sql_handler", fake_handler)

    state = {"query": "doanh thu tháng 2", "tool_calls": [], "config": {}}
    result = await mod.db_connector_node(state)

    assert result == {"db_results": [{"id": "sql_1", "content": "[]"}], "db_error": None}
    fake_handler.assert_called_once_with({"query": "doanh thu tháng 2"}, {})


async def test_maps_error_when_handler_returns_none(monkeypatch):
    import nodes.sandbox.db_connector as mod
    fake_handler = AsyncMock(return_value=([], [{"tool_name": "text_to_sql", "error": "no database connection configured"}]))
    monkeypatch.setattr(mod, "_text_to_sql_handler", fake_handler)

    state = {"query": "hello", "tool_calls": [], "config": {}}
    result = await mod.db_connector_node(state)

    assert result == {"db_results": [], "db_error": "no database connection configured"}


async def test_uses_db_query_tool_call_query_over_raw_state_query(monkeypatch):
    import nodes.sandbox.db_connector as mod
    fake_handler = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "_text_to_sql_handler", fake_handler)

    state = {
        "query": "raw user query",
        "tool_calls": [{"type": "db_query", "query": "refined query from planner"}],
        "config": {},
    }
    await mod.db_connector_node(state)

    fake_handler.assert_called_once_with({"query": "refined query from planner"}, {})


async def test_partial_success_still_reports_first_error(monkeypatch):
    """Multi-DB case: some queries succeed, some fail — db_results keeps the
    successes, db_error surfaces (the first) failure rather than silently
    dropping it."""
    import nodes.sandbox.db_connector as mod
    fake_handler = AsyncMock(return_value=(
        [{"id": "sql_1", "metadata": {"db_name": "Sales"}}],
        [{"tool_name": "text_to_sql", "db_name": "Warehouse", "error": "connection refused"}],
    ))
    monkeypatch.setattr(mod, "_text_to_sql_handler", fake_handler)

    state = {"query": "q", "tool_calls": [], "config": {}}
    result = await mod.db_connector_node(state)

    assert result["db_results"] == [{"id": "sql_1", "metadata": {"db_name": "Sales"}}]
    assert result["db_error"] == "connection refused"
