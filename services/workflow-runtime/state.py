from typing import Any, List, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    query: str
    original_query: Optional[str]
    messages: List[dict[str, Any]]
    memory_context: List[dict[str, Any]]
    workflow_memory_context: List[dict[str, Any]]  # doc §3.7 — past successful runs of the same task_type
    tool_calls: List[dict[str, Any]]
    kb_chunks: List[dict[str, Any]]
    kb_error: Optional[str]
    mcp_results: List[dict[str, Any]]
    mcp_errors: List[dict[str, Any]]
    tool_results: List[dict[str, Any]]
    tool_errors: List[dict[str, Any]]
    db_results: List[dict[str, Any]]            # set bởi db_connector_node — có thể gộp từ nhiều DB chạy song song
    db_error: Optional[str]
    chart_specs: List[dict[str, Any]]           # set bởi visualization_node — 1 phần tử/nguồn nối vào (không gộp chung khác schema)
    chart_error: Optional[str]        # best-effort — KHÔNG bounce về planner/judge_phase1
    rrf_results: List[dict[str, Any]]
    reranked_chunks: List[dict[str, Any]]
    response: str
    suggestions: List[str]
    intent: Optional[str]             # "question" | "task" — set by planner
    language: Optional[str]           # "vi" | "en" — set by planner
    loop_iteration: int               # số lần judge đã chạy (tăng mỗi lần judge FAIL)
    judge_verdict: Optional[str]      # "PASS" | "FAIL"
    judge_error_type: Optional[str]   # "PLANNING_ERROR" | "DATA_QUALITY" | "GROUNDING_ERROR"
    judge_reason: Optional[str]
    judge_phase1_error: Optional[str]  # "RETRIEVAL_ERROR" | "TOOL_ERROR" | None — set bởi judge_phase1_node
    human_approved: Optional[bool]    # None = chưa qua HITL node, True/False = kết quả review
    human_note: Optional[str]         # ghi chú reviewer khi reject
    injected_metadata: Optional[dict[str, Any]]  # set bởi metadata_injector_node — đọc bởi planner + responder
    config: dict[str, Any]
