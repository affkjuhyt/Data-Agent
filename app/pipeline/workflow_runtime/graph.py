from langgraph.graph import StateGraph, START, END
from app.pipeline.workflow_runtime.state import AgentState
from app.pipeline.workflow_runtime.nodes.guardrail import guardrail_input_node, guardrail_output_node
from app.pipeline.workflow_runtime.nodes.model_harness.planner import planner_node
from app.pipeline.workflow_runtime.nodes.model_harness.responder import responder_node
from app.pipeline.workflow_runtime.nodes.model_harness.rrf_ranking import rrf_ranking_node
from app.pipeline.workflow_runtime.nodes.model_harness.reranker import reranker_node
from app.pipeline.workflow_runtime.nodes.sandbox.kb_search import kb_search_node
from app.pipeline.workflow_runtime.nodes.sandbox.mcp import mcp_node

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


def build_graph(flow_nodes: list[dict] | None = None):
    if not flow_nodes:
        return _build_default_graph()
    return _build_dynamic_graph(flow_nodes)


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


def _build_dynamic_graph(flow_nodes: list[dict]):
    node_types = {
        (n.get("data", {}).get("nodeType") or n.get("type", ""))
        for n in flow_nodes
        if (n.get("data", {}).get("nodeType") or n.get("type", "")) in NODE_TYPE_MAP
    }

    builder = StateGraph(AgentState)

    builder.add_node("guardrail_input",  guardrail_input_node)
    builder.add_node("guardrail_output", guardrail_output_node)

    has_kb_search   = "kb_search"   in node_types
    has_mcp         = "mcp"         in node_types
    has_rrf         = "rrf_ranking" in node_types
    has_reranker    = "reranker"    in node_types
    has_responder   = "responder"   in node_types
    has_retrieval   = has_kb_search or has_mcp

    for nt in node_types:
        builder.add_node(nt, NODE_TYPE_MAP[nt])

    if "planner" not in node_types:
        builder.add_node("planner", planner_node)

    builder.add_edge(START, "guardrail_input")
    builder.add_conditional_edges(
        "guardrail_input",
        _route_after_guardrail_input,
        {"planner": "planner", "guardrail_output": "guardrail_output"},
    )

    if has_retrieval:
        builder.add_node("join_retrieval", join_retrieval_node)
        if has_kb_search:
            builder.add_conditional_edges("planner", route_after_planner, ["kb_search"] + (["mcp"] if has_mcp else []))
            builder.add_edge("kb_search", "join_retrieval")
        else:
            builder.add_conditional_edges("planner", route_after_planner, ["mcp"])
        if has_mcp:
            builder.add_edge("mcp", "join_retrieval")

        next_after_retrieval = "rrf_ranking" if has_rrf else ("reranker" if has_reranker else "responder")
        builder.add_edge("join_retrieval", next_after_retrieval)
    else:
        builder.add_edge("planner", "responder" if has_responder else "guardrail_output")

    if has_rrf:
        next_after_rrf = "reranker" if has_reranker else ("responder" if has_responder else "guardrail_output")
        builder.add_edge("rrf_ranking", next_after_rrf)

    if has_reranker:
        next_after_reranker = "responder" if has_responder else "guardrail_output"
        builder.add_edge("reranker", next_after_reranker)

    if has_responder:
        builder.add_edge("responder", "guardrail_output")

    builder.add_edge("guardrail_output", END)

    return builder.compile()
