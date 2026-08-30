"""db_connector_node (backend/services/workflow_runtime/nodes/sandbox/db_connector.py)
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
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

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
    # cfg is the agent config with this turn's slots overlaid — the handler
    # needs them to compile a follow-up as "previous request + one filter".
    call_args, call_cfg = fake_handler.call_args.args
    assert call_args == {"query": "doanh thu tháng 2"}
    assert call_cfg["slots"] is None


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

    call_args, call_cfg = fake_handler.call_args.args
    assert call_args == {"query": "refined query from planner"}
    assert call_cfg["slots"] is None


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


async def test_slots_from_state_reach_the_handler(monkeypatch):
    """Slots are per-turn state, not agent config, so they are overlaid at this
    node boundary. If that stops happening, every follow-up silently loses the
    previous turn's filters while all other tests keep passing."""
    import nodes.sandbox.db_connector as mod
    fake_handler = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "_text_to_sql_handler", fake_handler)

    slots = {"metric": "revenue_net", "filters": [{"dimension": "miền", "op": "eq", "values": ["Bắc"]}]}
    state = {"query": "chỉ lấy miền Bắc", "tool_calls": [], "config": {"tenant_id": "t"}, "slots": slots}

    await mod.db_connector_node(state)

    _, call_cfg = fake_handler.call_args.args
    assert call_cfg["slots"] == slots
    assert call_cfg["tenant_id"] == "t"
