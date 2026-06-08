from collections import defaultdict
from langgraph.graph import StateGraph, START, END
from state import AgentState
from nodes.guardrail import guardrail_input_node, guardrail_output_node
from nodes.model_harness.planner import planner_node
from nodes.model_harness.responder import responder_node
from nodes.model_harness.rrf_ranking import rrf_ranking_node
from nodes.model_harness.reranker import reranker_node
from nodes.sandbox.kb_search import kb_search_node
from nodes.sandbox.mcp import mcp_node

NODE_TYPE_MAP = {
    "planner":     planner_node,
    "kb_search":   kb_search_node,
    "mcp":         mcp_node,
    "rrf_ranking": rrf_ranking_node,
    "reranker":    reranker_node,
    "responder":   responder_node,
}

_DEFAULT_NODE_ORDER = ["planner", "kb_search", "mcp", "rrf_ranking", "reranker", "responder"]


def _route_after_guardrail_input(state: AgentState) -> str:
    return "guardrail_output" if state.get("guardrail_triggered") else "planner"


def route_after_planner(state: AgentState) -> list[str]:
    tool_calls = state.get("tool_calls", [])
    types = {tc.get("type") for tc in tool_calls}
    destinations = []
    if "kb_search" in types or not tool_calls:
        destinations.append("kb_search")
    if "mcp" in types:
        destinations.append("mcp")
    return destinations or ["kb_search"]


def join_retrieval_node(state: AgentState) -> dict:
    return {}


def build_graph(flow_nodes: list[dict] | None = None, flow_edges: list[dict] | None = None):
    """Build LangGraph graph.
    flow_nodes=None → default hardcoded graph (backward compat).
    flow_nodes+flow_edges → dynamic graph theo topology user vẽ trên canvas.
    """
    if not flow_nodes:
        return _build_default_graph()
    return _build_dynamic_graph(flow_nodes, flow_edges or [])


def _build_default_graph():
    builder = StateGraph(AgentState)

    builder.add_node("guardrail_input",  guardrail_input_node)
    builder.add_node("planner",          planner_node)
    builder.add_node("kb_search",        kb_search_node)
    builder.add_node("mcp",              mcp_node)
    builder.add_node("join_retrieval",   join_retrieval_node)
    builder.add_node("rrf_ranking",      rrf_ranking_node)
    builder.add_node("reranker",         reranker_node)
    builder.add_node("responder",        responder_node)
    builder.add_node("guardrail_output", guardrail_output_node)

    builder.add_edge(START, "guardrail_input")
    builder.add_conditional_edges(
        "guardrail_input",
        _route_after_guardrail_input,
        {"planner": "planner", "guardrail_output": "guardrail_output"},
    )
    builder.add_conditional_edges("planner", route_after_planner, ["kb_search", "mcp"])
    builder.add_edge("kb_search",        "join_retrieval")
    builder.add_edge("mcp",              "join_retrieval")
    builder.add_edge("join_retrieval",   "rrf_ranking")
    builder.add_edge("rrf_ranking",      "reranker")
    builder.add_edge("reranker",         "responder")
    builder.add_edge("responder",        "guardrail_output")
    builder.add_edge("guardrail_output", END)

    return builder.compile()


def _build_dynamic_graph(flow_nodes: list[dict], flow_edges: list[dict]):
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

    # Node types xuất hiện trong canvas edges
    node_types = {nt for pair in type_edges for nt in pair} & set(NODE_TYPE_MAP)

    builder = StateGraph(AgentState)

    # Guardrail luôn có (locked)
    builder.add_node("guardrail_input",  guardrail_input_node)
    builder.add_node("guardrail_output", guardrail_output_node)

    for nt in node_types:
        builder.add_node(nt, NODE_TYPE_MAP[nt])

    # Planner luôn có
    if "planner" not in node_types:
        builder.add_node("planner", planner_node)

    # START → guardrail_input → planner (hoặc guardrail_output nếu bị chặn)
    builder.add_edge(START, "guardrail_input")
    builder.add_conditional_edges(
        "guardrail_input",
        _route_after_guardrail_input,
        {"planner": "planner", "guardrail_output": "guardrail_output"},
    )

    # Tìm retrieval nodes mà planner nối tới trên canvas
    retrieval_nodes = {tgt for src, tgt in type_edges if src == "planner" and tgt in ("kb_search", "mcp")}
    non_retrieval_planner_targets = {tgt for src, tgt in type_edges if src == "planner" and tgt not in ("kb_search", "mcp")}

    if len(retrieval_nodes) > 1:
        # Nhiều retrieval node → cần join để sync parallel branches
        builder.add_node("join_retrieval", join_retrieval_node)
        builder.add_conditional_edges("planner", route_after_planner, list(retrieval_nodes))
        for rn in retrieval_nodes:
            builder.add_edge(rn, "join_retrieval")
        # join_retrieval → node tiếp theo theo canvas (node mà retrieval nodes nối tới)
        after_retrieval = {tgt for src, tgt in type_edges if src in retrieval_nodes}
        for tgt in after_retrieval:
            builder.add_edge("join_retrieval", tgt)
    elif len(retrieval_nodes) == 1:
        # 1 retrieval node → dùng conditional để planner quyết định runtime
        builder.add_conditional_edges("planner", route_after_planner, list(retrieval_nodes))
    elif non_retrieval_planner_targets:
        for tgt in non_retrieval_planner_targets:
            builder.add_edge("planner", tgt)
    else:
        builder.add_edge("planner", "guardrail_output")

    # Tính incoming edges của từng node (để detect fan-in)
    incoming: dict[str, set[str]] = defaultdict(set)
    for src, tgt in type_edges:
        incoming[tgt].add(src)

    # Node có >1 nguồn (không tính retrieval_nodes đã xử lý) → cần join tự động
    handled_sources = retrieval_nodes | {"planner", "guardrail_input", "guardrail_output"}
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

    # Node nào không có outgoing edge trên canvas → nối vào guardrail_output
    all_canvas_types = node_types | {"planner"}
    has_outgoing = {src for src, _ in type_edges}
    terminal_nodes = all_canvas_types - handled_sources - has_outgoing
    for tn in terminal_nodes:
        builder.add_edge(tn, "guardrail_output")

    builder.add_edge("guardrail_output", END)

    return builder.compile()
