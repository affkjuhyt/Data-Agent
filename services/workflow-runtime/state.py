from typing import List, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    query: str
    original_query: Optional[str]
    messages: List[dict]
    memory_context: List[dict]
    workflow_memory_context: List[dict]  # doc §3.7 — past successful runs of the same task_type
    tool_calls: List[dict]
    kb_chunks: List[dict]
    kb_error: Optional[str]
    mcp_results: List[dict]
    mcp_errors: List[dict]
    tool_results: List[dict]
    tool_errors: List[dict]
    rrf_results: List[dict]
    reranked_chunks: List[dict]
    response: str
    suggestions: List[str]
    guardrail_triggered: bool
    guardrail_message: str
    guardrail_stage: Optional[str]    # always "input"
    guardrail_reason: Optional[str]   # lý do chi tiết từ LLM hoặc regex pattern matched
    intent: Optional[str]             # "question" | "task" — set by planner
    language: Optional[str]           # "vi" | "en" — set by planner
    loop_iteration: int               # số lần judge đã chạy (tăng mỗi lần judge FAIL)
    judge_verdict: Optional[str]      # "PASS" | "FAIL"
    judge_error_type: Optional[str]   # "RETRIEVAL_ERROR" | "REASONING_ERROR" | "TOOL_ERROR" | "DATA_QUALITY"
    judge_reason: Optional[str]
    config: dict
