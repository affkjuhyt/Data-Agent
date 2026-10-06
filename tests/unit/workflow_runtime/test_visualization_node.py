"""visualization_node (backend/services/workflow_runtime/application/nodes/model_harness/visualization.py)
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

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
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


def _rs(rows: list[dict]) -> dict:
    """A ResultSet as db_connector writes it (result_sets.build)."""
    from services.workflow_runtime.application import result_sets

    return result_sets.build(rows, connection_id="dw", engine="postgres", generated_by="llm", sql="SELECT 1")


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
        "result_sets": [_rs([{"month": "01", "revenue": 100}])],
    }
    result = await mod.visualization_node(state)

    assert result == {}


async def test_db_connector_source_empty_rows_returns_chart_error():
    import nodes.model_harness.visualization as mod

    state = {
        "tool_calls": _viz_tool_calls(),
        "config": _cfg_with_edge("db_connector"),
        "result_sets": [],
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
        "result_sets": [_rs(rows)],
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
        "result_sets": [_rs([{"month": "01", "revenue": 100}])],
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
        "result_sets": [_rs([{"month": "01", "revenue": 100}])],
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
        "result_sets": [_rs([{"month": "01", "revenue": 100}]), _rs([{"month": "02", "revenue": 200}])],
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
        "result_sets": [_rs(rows)],
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
        "result_sets": [_rs([{"month": "01", "revenue": 100}])],
    }
    result = await mod.visualization_node(state)

    assert result["chart_specs"] == []
    assert result["chart_error"]


async def test_a_result_set_longer_than_its_preview_is_plotted_from_the_stored_rows(monkeypatch):
    """The preview is 50 rows; a year of daily points is not. The full rows are
    read back by result id rather than drawing the first 50 days."""
    import nodes.model_harness.visualization as mod

    monkeypatch.setattr(mod, "_pick_chart_schema", AsyncMock(return_value={
        "chart_type": "line", "title": "x", "x_key": "day", "y_keys": ["revenue"],
    }))
    rows = [{"day": f"2026-01-{d % 28 + 1:02d}", "revenue": d} for d in range(120)]
    rs = {**_rs(rows), "full_rows_available": True}
    load = AsyncMock(return_value={"rows": rows})
    monkeypatch.setattr(mod.answer_exports, "load_payload", load)

    result = await mod.visualization_node({
        "tool_calls": _viz_tool_calls(),
        "config": {**_cfg_with_edge("db_connector"), "tenant_id": "t1"},
        "result_sets": [rs],
    })

    assert len(rs["rows_preview"]) == 50
    assert len(result["chart_specs"][0]["data"]) == 120
    load.assert_awaited_once_with("t1", rs["result_id"])
