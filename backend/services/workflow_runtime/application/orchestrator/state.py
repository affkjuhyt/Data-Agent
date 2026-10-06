from typing import Any, Optional

from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    # Request context
    query: str
    rewritten_query: str
    user_id: str
    tenant_id: str
    user_ref: str
    # Real caller roles, forwarded to workflow_runtime so the agent asserts this
    # user downstream instead of a fixed admin set. Empty => kb_search fails closed.
    user_roles: list[str]
    session_id: str
    agent_id: Optional[str]       # None → multi agent mode
    messages: list[dict[str, Any]]          # conversation history từ client
    cfg: dict[str, Any]                     # global config (guardrail, rewrite, suggestion models)

    # Guardrail
    guardrail_triggered: bool
    guardrail_reason: str

    # Clarification HITL
    clarification_needed: bool
    clarification_questions: list[str]
    clarification_response: Optional[str]  # user trả lời sau interrupt
    # Options picked on the agent's clarification card ({reason, value}).
    clarification_answers: list[dict[str, Any]]
    # Single-agent mode: the agent conversation this turn continues.
    agent_conversation_id: Optional[str]

    # Execution
    agents: list[dict[str, Any]]            # published agents từ catalog
    plan: Optional[Any]           # Plan object từ ToT planner
    process: Optional[Any]        # PlanningProcess sống — cần cho report_node_failure()
                                   # và để session_cache lưu/khôi phục khi client retry
    completed_node_ids: list[str] # node đã chạy thành công (resume tránh gọi lại agent)
    final_response: str
    citations: list[dict[str, Any]]
    suggestions: list[str]
    # Set when single_agent/summary already produced the suggestions alongside
    # the answer replay, so suggestion_node does not call the model again.
    suggestions_ready: bool
    target_results: list[Any]
    recovered: bool
    success: bool
    # The single agent ended its turn with a `__refusal` (R0-7): no answer, so
    # no follow-up suggestions either.
    refused: bool

    # Timings (ms)
    planner_ms: int
    executor_ms: int
    summary_ms: int
