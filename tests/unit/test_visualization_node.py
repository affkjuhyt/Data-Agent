"""visualization_node (backend/services/workflow-runtime/nodes/model_harness/visualization.py)
— vẽ chart từ dữ liệu của node được nối cạnh vào (db_connector/mcp/kb_search/
tool_executor), xác định qua cfg["flow_nodes"]/cfg["flow_edges"]. Mỗi NGUỒN
(node type) nối vào → 1 chart_spec RIÊNG trong list chart_specs (không gộp
chung rows từ các nguồn khác schema vào 1 chart). Chỉ nhận content parse
được thành JSON list[dict]/dict (không hallucinate từ prose)."""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _cfg_with_edge(source_type: str) -> dict:
    flow_nodes = [
        {"id": "src1", "data": {"nodeType": source_type}},
        {"id": "viz1", "data": {"nodeType": "visualization"}},
    ]
    flow_edges = [{"source": "src1", "target": "viz1"}]
    return {"flow_nodes": flow_nodes, "flow_edges": flow_edges}


def _viz_tool_calls(query: str = "vẽ biểu đồ") -> list[dict]:
    return [{"type": "visualize", "query": query}]


async def test_no_visualize_tool_call_is_noop():
    import nodes.model_harness.visualization as mod

    state = {"tool_calls": [{"type": "db_query", "query": "x"}], "config": _cfg_with_edge("db_connector")}
    result = await mod.visualization_node(state)

    assert result == {}


async def test_no_incoming_edge_is_noop():
    import nodes.model_harness.visualization as mod

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": {"flow_nodes": [{"id": "viz1", "data": {"nodeType": "visualization"}}], "flow_edges": []},
        "db_results": [{"content": json.dumps([{"month": "01", "revenue": 100}])}],
    }
    result = await mod.visualization_node(state)

    assert result == {}


async def test_db_connector_source_empty_rows_returns_chart_error():
    import nodes.model_harness.visualization as mod

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [],
    }
    result = await mod.visualization_node(state)

    assert result == {
        "chart_specs": [],
        "chart_error": "Dữ liệu từ nguồn đã nối không ở dạng bảng hoặc không xác định được cách vẽ phù hợp",
    }


async def test_prose_content_from_kb_search_does_not_hallucinate():
    import nodes.model_harness.visualization as mod

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("kb_search"),
        "kb_chunks": [{"content": "Doanh thu công ty tăng trưởng tốt trong quý vừa qua."}],
    }
    result = await mod.visualization_node(state)

    assert result["chart_specs"] == []
    assert result["chart_error"]


async def test_valid_rows_calls_llm_and_maps_real_data(monkeypatch):
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(return_value={
        "chart_type": "line", "title": "Doanh thu theo tháng",
        "x_key": "month", "y_keys": ["revenue"],
    })
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    rows = [{"month": "01", "revenue": 100}, {"month": "02", "revenue": 200}]
    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [{"content": json.dumps(rows)}],
    }
    result = await mod.visualization_node(state)

    assert result["chart_error"] is None
    assert len(result["chart_specs"]) == 1
    assert result["chart_specs"][0]["chart_type"] == "line"
    assert result["chart_specs"][0]["data"] == rows  # dữ liệu THẬT, không phải LLM tự sinh
    fake_schema.assert_called_once()


async def test_llm_picks_unknown_x_key_returns_no_chart_for_that_source(monkeypatch):
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(return_value={
        "chart_type": "bar", "title": "x", "x_key": "not_a_column", "y_keys": ["revenue"],
    })
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [{"content": json.dumps([{"month": "01", "revenue": 100}])}],
    }
    result = await mod.visualization_node(state)

    assert result["chart_specs"] == []
    assert result["chart_error"]


async def test_multi_source_produces_one_chart_per_source_not_merged(monkeypatch):
    """DB Connector {month,revenue} và MCP {region,sales} có schema khác
    nhau — phải ra 2 chart_specs TÁCH BIỆT, không gộp chung 1 chart sai cột."""
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(side_effect=[
        {"chart_type": "line", "title": "Doanh thu", "x_key": "month", "y_keys": ["revenue"]},
        {"chart_type": "bar", "title": "Sales theo vùng", "x_key": "region", "y_keys": ["sales"]},
    ])
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    cfg = {
        "flow_nodes": [
            {"id": "db1", "data": {"nodeType": "db_connector"}},
            {"id": "mcp1", "data": {"nodeType": "mcp"}},
            {"id": "viz1", "data": {"nodeType": "visualization"}},
        ],
        "flow_edges": [
            {"source": "db1", "target": "viz1"},
            {"source": "mcp1", "target": "viz1"},
        ],
    }
    state = {
        "tool_calls": _viz_tool_calls(),
        "config": cfg,
        "db_results": [{"content": json.dumps([{"month": "01", "revenue": 100}])}],
        "mcp_results": [{"content": json.dumps([{"region": "North", "sales": 50}])}],
    }
    result = await mod.visualization_node(state)

    assert len(result["chart_specs"]) == 2
    x_keys = {s["x_key"] for s in result["chart_specs"]}
    assert x_keys == {"month", "region"}


async def test_multi_db_rows_within_same_source_are_merged_into_one_chart(monkeypatch):
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(return_value={
        "chart_type": "bar", "title": "x", "x_key": "month", "y_keys": ["revenue"],
    })
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [
            {"content": json.dumps([{"month": "01", "revenue": 100}])},
            {"content": json.dumps([{"month": "02", "revenue": 200}])},
        ],
    }
    result = await mod.visualization_node(state)

    assert len(result["chart_specs"]) == 1
    assert len(result["chart_specs"][0]["data"]) == 2


async def test_composed_chart_type_includes_series(monkeypatch):
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(return_value={
        "chart_type": "composed", "title": "Doanh thu & lợi nhuận", "x_key": "month",
        "y_keys": ["revenue", "profit"],
        "series": [{"key": "revenue", "type": "bar"}, {"key": "profit", "type": "line"}],
    })
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    rows = [{"month": "01", "revenue": 100, "profit": 20}]
    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [{"content": json.dumps(rows)}],
    }
    result = await mod.visualization_node(state)

    spec = result["chart_specs"][0]
    assert spec["chart_type"] == "composed"
    assert spec["series"] == [{"key": "revenue", "type": "bar"}, {"key": "profit", "type": "line"}]
    assert spec["data"] == rows


async def test_composed_chart_without_valid_series_is_dropped(monkeypatch):
    import nodes.model_harness.visualization as mod

    fake_schema = AsyncMock(return_value={
        "chart_type": "composed", "title": "x", "x_key": "month", "y_keys": ["revenue"],
        "series": [{"key": "not_a_column", "type": "bar"}],
    })
    monkeypatch.setattr(mod, "_pick_chart_schema", fake_schema)

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "db_results": [{"content": json.dumps([{"month": "01", "revenue": 100}])}],
    }
    result = await mod.visualization_node(state)

    assert result["chart_specs"] == []
    assert result["chart_error"]
