import json
import logging
from collections import defaultdict
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from services.workflow_runtime.application.nodes.context.metadata_injector import (
    metadata_injector_node,
)
from services.workflow_runtime.application.nodes.model_harness.human_approval import (
    human_approval_node,
)
from services.workflow_runtime.application.nodes.model_harness.judge import judge_node
from services.workflow_runtime.application.nodes.model_harness.planner import (
    PLANNER_FALLBACK_REASON,
    planner_node,
)
from services.workflow_runtime.application.nodes.model_harness.reranker import reranker_node
from services.workflow_runtime.application.nodes.model_harness.responder import responder_node
from services.workflow_runtime.application.nodes.model_harness.rrf_ranking import rrf_ranking_node
from services.workflow_runtime.application.nodes.model_harness.visualization import (
    visualization_node,
)
from services.workflow_runtime.application.nodes.sandbox.db_connector import db_connector_node
from services.workflow_runtime.application.nodes.sandbox.kb_search import kb_search_node
from services.workflow_runtime.application.nodes.sandbox.mcp import mcp_node
from services.workflow_runtime.application.nodes.sandbox.tool_executor import tool_executor_node
from services.workflow_runtime.domain.state import AgentState
from services.workflow_runtime.infrastructure.observability import get_langfuse_client

logger = logging.getLogger(__name__)

NODE_TYPE_MAP = {
    "planner":          planner_node,
    "kb_search":        kb_search_node,
    "mcp":              mcp_node,
    "tool_executor":    tool_executor_node,
    "db_connector":     db_connector_node,
    "rrf_ranking":      rrf_ranking_node,
    "reranker":         reranker_node,
    "visualization":    visualization_node,
    "responder":        responder_node,
    "judge":            judge_node,
    "human_approval":   human_approval_node,
    "metadata_injector": metadata_injector_node,
}

_DEFAULT_FLOW_NODES = [
    {"id": t, "data": {"nodeType": t}}
    for t in ("planner", "kb_search", "mcp", "rrf_ranking", "reranker", "responder")
]
_DEFAULT_FLOW_EDGES: list[dict[str, Any]] = [
    {"source": "planner",     "target": "kb_search"},
    {"source": "planner",     "target": "mcp"},
    {"source": "kb_search",   "target": "rrf_ranking"},
    {"source": "mcp",         "target": "rrf_ranking"},
    {"source": "rrf_ranking", "target": "reranker"},
    {"source": "reranker",    "target": "responder"},
]


def _make_route_after_planner(
    retrieval_nodes: frozenset[str], ask_target: str | None = None,
) -> Callable[[AgentState], list[str]]:
    """Closure giới hạn destinations trong các node thực sự có trong graph.

    `ask_target` (responder) is where a turn goes when the planner raised a
    clarification: nothing must execute. Without it the "no tool_calls"
    fallback below picked the first retrieval node — and a canvas whose first
    retrieval node is db_connector ran a query on the very metric the question
    was ambiguous about, then shipped the number as evidence next to the
    question. Found by the live E2E, invisible to the node-level unit tests."""
    def route_after_planner(state: AgentState) -> list[str]:
        if state.get("clarifications") and ask_target:
            return [ask_target]
        tool_calls = state.get("tool_calls", [])
        types = {tc.get("type") for tc in tool_calls}
        destinations = []
        if ("kb_search" in types or not tool_calls) and "kb_search" in retrieval_nodes:
            destinations.append("kb_search")
        if "mcp" in types and "mcp" in retrieval_nodes:
            destinations.append("mcp")
        if "tool" in types and "tool_executor" in retrieval_nodes:
            destinations.append("tool_executor")
        if "db_query" in types and "db_connector" in retrieval_nodes:
            destinations.append("db_connector")
        # Fallback: dùng node retrieval đầu tiên có trong graph
        return destinations or [next(iter(retrieval_nodes))]
    return route_after_planner


def join_retrieval_node(state: AgentState) -> dict[str, Any]:
    return {}


_JUDGE_PHASE1_NODE = "judge_phase1"


def _plan_signature(tool_calls: list[dict[str, Any]], query: str) -> str:
    """What a tool plan asks for: type, tool, normalised query and arguments per
    call, order-free. A call without a query searches the user's question, as
    kb_search does."""
    calls = sorted(
        json.dumps(
            [
                tc.get("type"),
                tc.get("tool_name"),
                " ".join(str(tc.get("query") or query).casefold().split()),
                tc.get("arguments") or {},
            ],
            ensure_ascii=False, sort_keys=True, default=str,
        )
        for tc in tool_calls
    )
    return "\n".join(calls)


_PHASE1_REASONS = {
    "RETRIEVAL_ERROR": "Không tìm thấy dữ liệu liên quan — thử diễn đạt lại câu hỏi hoặc dùng nguồn/tool khác.",
    "TOOL_ERROR": "Công cụ đã thử nhiều lần nhưng vẫn lỗi — thử cách tiếp cận hoặc tool khác.",
}


async def judge_phase1_node(state: AgentState) -> dict[str, Any]:
    """Judge Pha 1 (mới) — check thuần state, không LLM, ngay sau khi retrieval
    xong (trước rrf_ranking/reranker/responder). Bắt RETRIEVAL_ERROR (rỗng
    toàn bộ — kb_chunks/mcp_results/tool_results/result_sets đều không có gì) và
    TOOL_ERROR (tool_errors/db_error không rỗng — tool như DB Connector đã tự
    sinh SQL + tự chạy + tự sửa lỗi kỹ thuật nội bộ tối đa 3 lần trước khi báo
    lỗi ra đây, nên không phải lỗi tạm thời). Reset đúng phần cần sinh lại, route
    thẳng về planner ngay — tiết kiệm chi phí rrf_ranking/reranker/responder
    cho các lỗi biết được sớm mà không cần LLM.

    Chỉ quay lại planner khi planner KHÔNG quyết định được gì (output không
    parse được → plan dự phòng PLANNER_FALLBACK_REASON). Một plan planner đã
    chọn rõ ràng mà vẫn rỗng hoặc lỗi (KB trống, tool đã tự sửa mà vẫn lỗi) thì
    đi tiếp để responder nói "không tìm thấy": hỏi lại planner chỉ ra đúng plan
    cũ (đo thực tế: 3 lần plan y hệt trên KB trống, ~1,3 s mỗi lần).

    CỐ Ý không cố phân biệt "data thật sự không tồn tại" với "query sai
    logic nên ra 0 dòng" (ví dụ SQL chạy thành công nhưng row_count=0) —
    2 trường hợp này nhìn giống hệt nhau ở tầng state (tool_results vẫn có 1
    phần tử, chỉ là content rỗng), và mỗi tool lại có 1 dạng "rỗng" khác
    nhau (SQL/pandas/web_search...) nên cố generic-hoá việc phát hiện ở đây
    rất dễ vỡ. Case đó CỐ TÌNH bỏ qua, để trôi tới responder → Judge Pha 2
    (đã có rule PLANNING_ERROR: "context không đủ") — nơi duy nhất có đủ
    ngữ nghĩa (LLM) để phán đoán "0 dòng này có hợp lý với câu hỏi không".

    Dùng CHUNG bộ đếm loop_iteration với Judge Pha 2 (giới hạn tổng số lần
    retry, mặc định loop_max_iterations=3) — nếu không, khi data THẬT SỰ
    không tồn tại (không phải lỗi tạm thời), planner/retrieval sẽ bị bounce
    vô hạn không có điểm dừng. Hết hạn mức lượt thử chung (dù lỗi tới từ
    Pha 1 hay Pha 2) → cho đi tiếp để responder tự trả lời kiểu "không tìm
    thấy thông tin", thay vì lặp mãi.

    Cũng set "judge_reason" (không chỉ judge_phase1_error) — planner_node
    chỉ đọc field này để tự sửa chiến lược ở lần thử sau (xem
    nodes/model_harness/planner.py); nếu bỏ trống, planner sẽ không biết
    cần đổi gì và có thể lặp lại y hệt query cũ.

    Nếu planner không hề yêu cầu tool/KB nào (tool_calls rỗng — ví dụ câu
    chào hỏi thông thường, không cần tra cứu), thì việc kb_chunks/tool_results
    rỗng là ĐÚNG NHƯ THIẾT KẾ (route_after_planner có fallback ép chạy
    kb_search dù tool_calls rỗng — xem graph.py:_make_route_after_planner),
    KHÔNG phải lỗi retrieval — không được phạt/bounce planner trong trường
    hợp này, nếu không mọi lời chào đơn giản sẽ bị lặp tốn kém vô ích.

    ASYNC dù thân hàm không await gì — bắt buộc, KHÔNG được đổi lại thành sync.
    LangGraph chạy node sync qua threadpool executor, làm mất OTEL contextvars
    mà Langfuse SDK v3 (OTEL-based) dùng để nối span vào đúng cha — planner_node
    từng dính chính xác lỗi này (xem commit c41ea72 "refactor langfuse": sync
    → async là cách fix, không phải thêm await nào). Biểu hiện quan sát được:
    judge_phase1 xuất hiện 2 lần trong 1 trace — 1 bản lồng đúng chỗ dưới
    span "single-agent", 1 bản rơi ra thành span mồ côi ở top-level (mất
    parent_span_id) — chỉ node sync mới bị, các node async khác (planner,
    kb_search, responder...) không bị.

    Khi PASS (judge_phase1_error=None) hạ level span xuống DEBUG — Langfuse
    UI mặc định ẩn observation level DEBUG khỏi trace view. Node này chỉ có
    giá trị quan sát lúc THẬT SỰ bounce về planner; trên happy path (luôn
    PASS) nó chỉ là nhiễu 1-2ms trong trace, không nên hiện mặc định."""
    cfg = state.get("config", {})
    max_iter = cfg.get("loop_max_iterations", 3)
    iteration = state.get("loop_iteration", 0)
    lf = get_langfuse_client()

    def _pass() -> dict[str, Any]:
        if lf:
            lf.update_current_span(level="DEBUG")
        return {"judge_phase1_error": None}

    if not state.get("tool_calls"):
        return _pass()

    error_type = None
    if state.get("tool_errors") or state.get("db_error"):
        error_type = "TOOL_ERROR"
    elif not (state.get("kb_chunks") or state.get("mcp_results") or state.get("tool_results") or state.get("result_sets")):
        error_type = "RETRIEVAL_ERROR"

    if error_type is None:
        return _pass()

    # Retry only a plan the planner did not actually decide. A decided plan that
    # came back empty (an empty knowledge base) or failed after the tool's own
    # corrections has its answer: say nothing was found.
    if not any(tc.get("reason") == PLANNER_FALLBACK_REASON for tc in state.get("tool_calls") or []):
        logger.info("judge_phase1 | %s on a decided plan → answer from what came back, no retry", error_type)
        return _pass()

    # Running the same plan again returns the same nothing — and a planner told
    # to change strategy often emits the identical plan anyway (seen live: three
    # identical re-plans against an empty knowledge base, ~1.3 s each). Stop at
    # the first repeat instead of spending the rest of loop_max_iterations.
    signature = _plan_signature(state.get("tool_calls") or [], state.get("query", ""))
    attempted = list(state.get("attempted_plans") or [])
    if signature in attempted:
        logger.info("judge_phase1 | planner repeated a plan that already returned %s → stop retrying", error_type)
        return _pass()

    if iteration >= max_iter:
        logger.warning("judge_phase1 | max_iterations=%d reached → force pass", max_iter)
        return _pass()

    return {
        "judge_phase1_error": error_type,
        "judge_reason": _PHASE1_REASONS[error_type],
        "loop_iteration": iteration + 1,
        "attempted_plans": attempted + [signature],
        "kb_chunks": [], "mcp_results": [], "tool_results": [], "tool_errors": [],
        # None, not []: result_sets is an appending channel (domain/state.py).
        "result_sets": None, "db_error": None,
        "rrf_results": [], "reranked_chunks": [],
        "chart_specs": [], "chart_error": None,
    }


def _make_route_after_phase1(after_targets: list[str]) -> Callable[[AgentState], list[str]]:
    def route_after_phase1(state: AgentState) -> list[str]:
        return ["planner"] if state.get("judge_phase1_error") else after_targets
    return route_after_phase1


def _make_route_after_judge(available: frozenset[str]) -> Callable[[AgentState], str]:
    """Closure: route về node lỗi, chỉ route tới node thực sự có trong graph.
    RETRIEVAL_ERROR/TOOL_ERROR không còn xuất hiện ở đây — Judge Pha 1
    (judge_phase1_node, chạy trước rrf_ranking/reranker/responder) đã bắt 2
    loại đó rồi, judge_node (Pha 2, sau responder) giờ chỉ còn PLANNING_ERROR/
    DATA_QUALITY/GROUNDING_ERROR."""
    def route_after_judge(state: AgentState) -> str:
        # Asking IS the answer for this turn: re-planning would only ask again
        # (or, worse, stop asking and guess). Nothing to grade, nothing to retry.
        if state.get("clarifications"):
            return "end"
        if state.get("judge_verdict") != "FAIL":
            return "end"
        error_type = state.get("judge_error_type", "")
        if error_type == "PLANNING_ERROR" and "planner" in available:
            return "planner"
        if error_type == "GROUNDING_ERROR" and "responder" in available:
            return "responder"
        return "end"  # DATA_QUALITY hoặc node không tồn tại trong graph
    return route_after_judge


def _make_route_after_human_approval(targets: frozenset[str]) -> Callable[[AgentState], str]:
    def route_after_human_approval(state: AgentState) -> str:
        if state.get("human_approved") is False:
            return "end"
        lst = list(targets)
        return lst[0] if lst else "end"
    return route_after_human_approval


def build_graph(
    flow_nodes: list[dict[str, Any]] | None = None,
    flow_edges: list[dict[str, Any]] | None = None,
    checkpointer: Any = None,
) -> Any:
    return _build_dynamic_graph(
        flow_nodes or _DEFAULT_FLOW_NODES,
        (flow_edges if flow_nodes else _DEFAULT_FLOW_EDGES) or [],
        checkpointer=checkpointer,
    )


def _build_dynamic_graph(flow_nodes: list[dict[str, Any]], flow_edges: list[dict[str, Any]], checkpointer: Any = None) -> Any:
    # Build node id → nodeType map (chỉ lấy node có trong NODE_TYPE_MAP)
    id_to_type: dict[str, str] = {}
    for n in flow_nodes:
        nid = n.get("id")
        ntype = n.get("data", {}).get("nodeType") or n.get("type", "")
        if nid and ntype in NODE_TYPE_MAP:
            id_to_type[nid] = ntype

    # Translate React Flow edges (node id) → (nodeType, nodeType)
    type_edges: set[tuple[str, str]] = set()
    for e in flow_edges:
        src = id_to_type.get(e.get("source", ""))
        tgt = id_to_type.get(e.get("target", ""))
        if src and tgt and src != tgt:
            type_edges.add((src, tgt))

    # Node types xuất hiện trong canvas edges — metadata_injector bị loại trừ
    # ở đây vì được wiring cố định thủ công bên dưới (không phụ thuộc edge).
    node_types = ({nt for pair in type_edges for nt in pair} & set(NODE_TYPE_MAP)) - {"metadata_injector"}

    builder = StateGraph(AgentState)

    for nt in node_types:
        builder.add_node(nt, NODE_TYPE_MAP[nt])

    # Planner luôn có
    if "planner" not in node_types:
        builder.add_node("planner", planner_node)

    # Metadata Injector: vị trí CỐ ĐỊNH ngay sau START, trước planner — không
    # phụ thuộc cạnh (edge) người dùng vẽ trên canvas, chỉ cần node có mặt
    # trong flow_nodes (kéo vào canvas = bật, không kéo = tắt hoàn toàn,
    # không có config bật/tắt riêng). Dùng id_to_type.values() (không phải
    # node_types, vốn chỉ chứa node có edge) vì node này không cần người
    # dùng tự nối cạnh.
    has_metadata_injector = "metadata_injector" in id_to_type.values()
    if has_metadata_injector:
        builder.add_node("metadata_injector", metadata_injector_node)
        builder.add_edge(START, "metadata_injector")
        builder.add_edge("metadata_injector", "planner")
    else:
        # START → planner trực tiếp (guardrail + rewrite đã được xử lý ở orchestrator)
        builder.add_edge(START, "planner")

    # Tìm retrieval nodes mà planner nối tới trên canvas
    retrieval_nodes = {tgt for src, tgt in type_edges if src == "planner" and tgt in ("kb_search", "mcp", "tool_executor", "db_connector")}
    non_retrieval_planner_targets = {tgt for src, tgt in type_edges if src == "planner" and tgt not in ("kb_search", "mcp")}

    # A clarification skips retrieval and goes straight to the responder, which
    # renders the question (see _make_route_after_planner).
    ask_target = "responder" if "responder" in node_types else None
    planner_targets = list(retrieval_nodes) + ([ask_target] if ask_target else [])
    _route_planner = _make_route_after_planner(frozenset(retrieval_nodes), ask_target)

    if len(retrieval_nodes) > 1:
        # Nhiều retrieval node → cần join để sync parallel branches
        builder.add_node("join_retrieval", join_retrieval_node)
        builder.add_conditional_edges("planner", _route_planner, planner_targets)
        for rn in retrieval_nodes:
            builder.add_edge(rn, "join_retrieval")
        # join_retrieval → Judge Pha 1 (check rẻ, không LLM) → node tiếp theo
        # theo canvas, hoặc quay lại planner ngay nếu retrieval/tool lỗi.
        after_retrieval = list({tgt for src, tgt in type_edges if src in retrieval_nodes})
        if after_retrieval:
            builder.add_node(_JUDGE_PHASE1_NODE, judge_phase1_node)
            builder.add_edge("join_retrieval", _JUDGE_PHASE1_NODE)
            builder.add_conditional_edges(
                _JUDGE_PHASE1_NODE,
                _make_route_after_phase1(after_retrieval),
                after_retrieval + ["planner"],
            )
    elif len(retrieval_nodes) == 1:
        # 1 retrieval node → dùng conditional để planner quyết định runtime
        builder.add_conditional_edges("planner", _route_planner, planner_targets)
        single_rn = next(iter(retrieval_nodes))
        after_retrieval = list({tgt for src, tgt in type_edges if src == single_rn})
        if after_retrieval:
            builder.add_node(_JUDGE_PHASE1_NODE, judge_phase1_node)
            builder.add_edge(single_rn, _JUDGE_PHASE1_NODE)
            builder.add_conditional_edges(
                _JUDGE_PHASE1_NODE,
                _make_route_after_phase1(after_retrieval),
                after_retrieval + ["planner"],
            )
    elif non_retrieval_planner_targets:
        for tgt in non_retrieval_planner_targets:
            builder.add_edge("planner", tgt)
    else:
        builder.add_edge("planner", END)

    has_judge          = "judge" in node_types
    has_responder      = "responder" in node_types
    has_human_approval = "human_approval" in node_types

    # Tính incoming edges của từng node (để detect fan-in)
    incoming: dict[str, set[str]] = defaultdict(set)
    for src, tgt in type_edges:
        incoming[tgt].add(src)

    # Node có >1 nguồn (không tính retrieval_nodes đã xử lý) → cần join tự động
    # responder, judge, human_approval được xử lý thủ công → bỏ qua trong generic loop
    handled_sources = retrieval_nodes | {"planner"}
    if has_responder:
        handled_sources = handled_sources | {"responder"}
    if has_judge:
        handled_sources = handled_sources | {"judge"}
    if has_human_approval:
        handled_sources = handled_sources | {"human_approval"}
    fan_in_nodes = {
        tgt for tgt, srcs in incoming.items()
        if len(srcs) > 1 and tgt not in handled_sources and tgt in node_types
        and not srcs.issubset(retrieval_nodes)  # retrieval đã có join_retrieval
    }
    for fan_in in fan_in_nodes:
        join_id = f"join_{fan_in}"
        builder.add_node(join_id, join_retrieval_node)
        for src in incoming[fan_in]:
            if src not in handled_sources:
                builder.add_edge(src, join_id)
        builder.add_edge(join_id, fan_in)

    # Thêm các canvas edges còn lại (bỏ qua những edge đã xử lý ở trên)
    skip_tgts = fan_in_nodes  # edge vào fan_in_nodes đã được route qua join
    for src, tgt in type_edges:
        if src in handled_sources:
            continue
        if tgt in skip_tgts:
            continue  # đã thêm qua join node ở trên
        builder.add_edge(src, tgt)

    # Node nào không có outgoing edge trên canvas → terminal
    all_canvas_types = node_types | {"planner"}
    has_outgoing = {src for src, _ in type_edges}
    terminal_nodes = all_canvas_types - handled_sources - has_outgoing

    if has_responder:
        if has_judge:
            builder.add_node("join_end", lambda state: {})
            builder.add_edge("responder", "join_end")
            builder.add_edge("join_end", "judge")

            available_for_judge = frozenset(
                {"planner", "responder"} | (node_types & {"kb_search", "tool_executor", "mcp", "db_connector"})
            )
            judge_path_map = {t: t for t in available_for_judge}
            judge_path_map["end"] = END
            builder.add_conditional_edges(
                "judge", _make_route_after_judge(available_for_judge), judge_path_map
            )
        else:
            builder.add_edge("responder", END)

        terminal_nodes = terminal_nodes - {"responder"}
        if has_judge:
            terminal_nodes = terminal_nodes - {"judge"}

    if has_human_approval:
        ha_targets = frozenset(
            tgt for src, tgt in type_edges
            if src == "human_approval" and tgt in node_types
        )
        ha_path_map = {t: t for t in ha_targets}
        ha_path_map["end"] = END
        builder.add_conditional_edges(
            "human_approval",
            _make_route_after_human_approval(ha_targets),
            ha_path_map,
        )
        terminal_nodes = terminal_nodes - {"human_approval"}

    for tn in terminal_nodes:
        builder.add_edge(tn, END)

    return builder.compile(checkpointer=checkpointer)
