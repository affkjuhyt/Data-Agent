from typing import Any, Optional
from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    # Request context
    query: str
    rewritten_query: str
    user_id: str
    tenant_id: str
    user_ref: str
    session_id: str
    agent_id: Optional[str]       # None → multi agent mode
    messages: list[dict]          # conversation history từ client
    cfg: dict                     # global config (guardrail, rewrite, suggestion models)

    # Guardrail
    guardrail_triggered: bool
    guardrail_reason: str

    # Clarification HITL
    clarification_needed: bool
    clarification_questions: list[str]
    clarification_response: Optional[str]  # user trả lời sau interrupt

    # Execution
    agents: list[dict]            # published agents từ catalog
    plan: Optional[Any]           # Plan object từ ToT planner
    final_response: str
    suggestions: list[str]
    target_results: list[Any]
    recovered: bool
    success: bool

    # Timings (ms)
    planner_ms: int
    executor_ms: int
    summary_ms: int
