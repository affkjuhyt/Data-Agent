"""Unit tests for Judge Pha 1 (backend/services/workflow_runtime/graph.py:judge_phase1_node)
— check thuần state, KHÔNG gọi LLM. Bắt RETRIEVAL_ERROR (rỗng toàn bộ) và
TOOL_ERROR (tool_errors không rỗng — tool như text_to_sql đã tự sửa lỗi kỹ
thuật nội bộ tối đa 3 lần trước khi báo lỗi ra đây) — nhưng CHỈ khi planner
thật sự có yêu cầu tool/KB (tool_calls không rỗng); nếu planner tự quyết
định không cần tool nào (ví dụ câu chào hỏi), rỗng là đúng như thiết kế,
không được bounce planner (bug tìm thấy qua Langfuse trace thực tế: câu
"xin chào" bị lặp 3 lần, mất 59s, vì route_after_planner có fallback ép
chạy kb_search dù tool_calls rỗng).

judge_phase1_node là ASYNC dù thân hàm không await gì — bắt buộc để LangGraph
không đẩy nó qua threadpool executor (làm mất OTEL contextvars mà Langfuse
SDK v3 dùng để nối span vào đúng cha), y hệt lỗi planner_node từng dính,
fix ở commit c41ea72. Test ở đây vẫn phải `await` nó."""
from __future__ import annotations

import pathlib
import sys

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


_KB_TOOL_CALL = [{"type": "kb_search", "query": "test", "reason": "", "confidence": 1.0}]


async def test_retrieval_error_when_everything_empty_and_tool_calls_requested():
    import graph as graph_mod

    state = {
        "tool_calls": _KB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result["judge_phase1_error"] == "RETRIEVAL_ERROR"
    assert result["kb_chunks"] == []
    assert result["tool_results"] == []
    assert result["tool_errors"] == []
    assert result["reranked_chunks"] == []
    # judge_reason phải có nội dung — planner_node CHỈ đọc field này để tự
    # sửa chiến lược ở lần thử sau (không đọc judge_phase1_error).
    assert result["judge_reason"]
    assert result["loop_iteration"] == 1


async def test_tool_error_when_tool_errors_present_even_with_other_data():
    import graph as graph_mod

    # kb_chunks có data, nhưng tool_errors không rỗng — vẫn phải chặn (quyết
    # định đã chốt: bất kỳ tool_errors nào cũng bounce về planner, vì tool đã
    # tự thử sửa nội bộ 3 lần rồi mới báo lỗi ra tới đây).
    state = {
        "tool_calls": [{"type": "tool", "tool_name": "text_to_sql", "query": "test"}],
        "kb_chunks": [{"id": "1", "content": "x"}],
        "mcp_results": [],
        "tool_results": [],
        "tool_errors": [{"tool_name": "text_to_sql", "error": "failed after 3 attempts"}],
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result["judge_phase1_error"] == "TOOL_ERROR"
    assert result["tool_results"] == []
    assert result["tool_errors"] == []
    assert result["kb_chunks"] == []
    assert result["reranked_chunks"] == []
    assert result["judge_reason"]
    assert result["loop_iteration"] == 1


async def test_passes_when_tool_calls_empty_even_if_everything_else_empty():
    """Regression test — bug thật phát hiện qua Langfuse trace: câu "xin
    chào" khiến planner trả tool_calls=[] (đúng, không cần tra cứu), nhưng
    route_after_planner vẫn ép chạy kb_search (fallback `or not tool_calls`),
    trả rỗng — TRƯỚC KHI có test này, judge_phase1 coi đây là RETRIEVAL_ERROR
    và bounce về planner 3 lần, mất ~59s cho 1 câu chào. Rỗng ở đây là ĐÚNG
    như thiết kế (planner chủ động không yêu cầu gì), không phải lỗi."""
    import graph as graph_mod

    state = {
        "tool_calls": [],
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


async def test_passes_when_tool_calls_missing_from_state():
    import graph as graph_mod

    state = {"kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": []}
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


async def test_loop_iteration_increments_each_bounce():
    import graph as graph_mod

    state = {
        "tool_calls": _KB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "loop_iteration": 1,
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result["judge_phase1_error"] == "RETRIEVAL_ERROR"
    assert result["loop_iteration"] == 2


async def test_force_pass_when_loop_max_iterations_reached():
    """Dùng chung hạn mức lượt thử (loop_iteration) với Judge Pha 2 — nếu
    data THẬT SỰ không tồn tại (không phải lỗi tạm thời), phải có điểm
    dừng, không được bounce vô hạn về planner."""
    import graph as graph_mod

    state = {
        "tool_calls": _KB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "loop_iteration": 3,   # đã chạm loop_max_iterations mặc định (3)
        "config": {},
    }
    result = await graph_mod.judge_phase1_node(state)

    # Hết hạn mức → cho đi tiếp (không bounce nữa), dù vẫn đang rỗng.
    assert result == {"judge_phase1_error": None}


async def test_force_pass_respects_custom_loop_max_iterations_from_config():
    import graph as graph_mod

    state = {
        "tool_calls": _KB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "loop_iteration": 1,
        "config": {"loop_max_iterations": 1},   # workflow này chỉ cho phép 1 lần retry
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


async def test_passes_when_any_retrieval_source_has_data_and_no_tool_errors():
    import graph as graph_mod

    state = {
        "tool_calls": _KB_TOOL_CALL,
        "kb_chunks": [{"id": "1", "content": "x"}],
        "mcp_results": [],
        "tool_results": [],
        "tool_errors": [],
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


async def test_passes_when_only_tool_results_has_data():
    import graph as graph_mod

    # kb_chunks/mcp_results rỗng là bình thường nếu planner chỉ gọi tool —
    # không nên bị coi là RETRIEVAL_ERROR miễn tool_results có gì.
    state = {
        "tool_calls": [{"type": "tool", "tool_name": "text_to_sql", "query": "test"}],
        "kb_chunks": [],
        "mcp_results": [],
        "tool_results": [{"id": "sql_1", "content": "data"}],
        "tool_errors": [],
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


_DB_TOOL_CALL = [{"type": "db_query", "query": "test"}]


async def test_tool_error_when_db_error_present():
    import graph as graph_mod

    # db_error không rỗng — DB Connector (giống text_to_sql trước đây) đã tự
    # thử sửa SQL nội bộ tối đa 3 lần trước khi báo lỗi ra đây.
    state = {
        "tool_calls": _DB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "db_results": [], "db_error": "failed after 3 attempts: connection refused",
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result["judge_phase1_error"] == "TOOL_ERROR"
    assert result["db_results"] == []
    assert result["db_error"] is None
    assert result["kb_chunks"] == [] and result["tool_results"] == []
    assert result["judge_reason"]
    assert result["loop_iteration"] == 1


async def test_retrieval_error_when_db_results_also_empty():
    import graph as graph_mod

    state = {
        "tool_calls": _DB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "db_results": [], "db_error": None,
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result["judge_phase1_error"] == "RETRIEVAL_ERROR"
    assert result["db_results"] == []
    assert result["db_error"] is None


async def test_passes_when_only_db_results_has_data():
    import graph as graph_mod

    # kb_chunks/mcp_results/tool_results rỗng là bình thường nếu planner chỉ
    # gọi DB Connector — không nên bị coi là RETRIEVAL_ERROR miễn db_results có gì.
    state = {
        "tool_calls": _DB_TOOL_CALL,
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        "db_results": [{"id": "sql_1", "content": "data"}], "db_error": None,
    }
    result = await graph_mod.judge_phase1_node(state)

    assert result == {"judge_phase1_error": None}


def test_route_after_phase1_blocks_to_planner_on_error():
    import graph as graph_mod

    route = graph_mod._make_route_after_phase1(["rrf_ranking"])

    assert route({"judge_phase1_error": "TOOL_ERROR"}) == ["planner"]
    assert route({"judge_phase1_error": "RETRIEVAL_ERROR"}) == ["planner"]
    assert route({"judge_phase1_error": None}) == ["rrf_ranking"]
