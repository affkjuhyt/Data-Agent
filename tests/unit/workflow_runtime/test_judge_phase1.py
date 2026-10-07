"""Unit tests for Judge Pha 1 (backend/services/workflow_runtime/application/graph.py:judge_phase1_node)
— check thuần state, KHÔNG gọi LLM. Bắt RETRIEVAL_ERROR (rỗng toàn bộ) và
TOOL_ERROR (tool_errors không rỗng — tool như text_to_sql đã tự sửa lỗi kỹ
thuật nội bộ tối đa 3 lần trước khi báo lỗi ra đây) — nhưng CHỈ khi planner
thật sự có yêu cầu tool/KB (tool_calls không rỗng); nếu planner tự quyết
định không cần tool nào (ví dụ câu chào hỏi), rỗng là đúng như thiết kế,
không được bounce planner (bug tìm thấy qua Langfuse trace thực tế: câu
"xin chào" bị lặp 3 lần, mất 59s, vì route_after_planner có fallback ép
chạy kb_search dù tool_calls rỗng).

Từ R0-7 không còn plan dự phòng (planner hỏng hai lần → PLANNER_ERROR, xem
test_structured_errors.py), nên mọi plan tới đây đều do planner quyết định, và
một plan đã quyết định mà rỗng/lỗi thì đi tiếp: hỏi lại planner chỉ ra đúng plan
cũ (đo thực tế trên KB trống: 3 lần plan y hệt). Node chỉ còn ghi nhận lỗi;
R0-11 sẽ gán lớp lỗi và định tuyến ở đây.

judge_phase1_node là ASYNC dù thân hàm không await gì — bắt buộc để LangGraph
không đẩy nó qua threadpool executor (làm mất OTEL contextvars mà Langfuse
SDK v3 dùng để nối span vào đúng cha), y hệt lỗi planner_node từng dính,
fix ở commit c41ea72. Test ở đây vẫn phải `await` nó."""
from __future__ import annotations

import pathlib
import sys

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


_EMPTY = {"kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": []}


async def test_an_empty_result_is_answered_not_retried():
    """An empty knowledge base: the planner chose kb_search and nothing matched.
    Asking the planner again returns the same plan — answer "not found" instead."""
    import graph as graph_mod

    result = await graph_mod.judge_phase1_node({
        "tool_calls": [{"type": "kb_search", "query": "test"}], **_EMPTY, "result_sets": [],
    })

    assert result == {"judge_phase1_error": None}


async def test_a_tool_error_is_answered_not_retried():
    """The tool already corrected itself up to three times before reporting;
    nothing is reset, so the responder still sees what did come back."""
    import graph as graph_mod

    result = await graph_mod.judge_phase1_node({
        "tool_calls": [{"type": "db_query", "query": "test"}],
        "kb_chunks": [{"id": "1", "content": "x"}], "mcp_results": [], "tool_results": [],
        "tool_errors": [], "db_error": "failed after 3 attempts: connection refused",
    })

    assert result == {"judge_phase1_error": None}


async def test_passes_when_tool_calls_empty_even_if_everything_else_empty():
    """Regression test — bug thật phát hiện qua Langfuse trace: câu "xin
    chào" khiến planner trả tool_calls=[] (đúng, không cần tra cứu), nhưng
    route_after_planner vẫn ép chạy kb_search (fallback `or not tool_calls`),
    trả rỗng — judge_phase1 từng coi đây là RETRIEVAL_ERROR và bounce về
    planner 3 lần, mất ~59s cho 1 câu chào."""
    import graph as graph_mod

    result = await graph_mod.judge_phase1_node({"tool_calls": [], **_EMPTY})

    assert result == {"judge_phase1_error": None}


def test_route_after_phase1_blocks_to_planner_on_error():
    """Kept for R0-11, which sets judge_phase1_error again per error class."""
    import graph as graph_mod

    route = graph_mod._make_route_after_phase1(["rrf_ranking"])

    assert route({"judge_phase1_error": "TOOL_ERROR"}) == ["planner"]
    assert route({"judge_phase1_error": None}) == ["rrf_ranking"]
