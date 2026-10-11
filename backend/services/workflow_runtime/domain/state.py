from typing import Annotated, Any, List, Optional

from typing_extensions import TypedDict


def merge_result_sets(
    current: list[dict[str, Any]], update: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Reducer for `result_sets`: producers add, None clears.

    db_connector and tool_executor can finish in the same step, and a plain
    channel accepts one write per step. A list from either is appended; None is
    how a retry (judge PLANNING_ERROR) and a new turn on a
    checkpointed thread start over — an empty list cannot mean "clear", since
    a producer with nothing to add writes exactly that.
    """
    if update is None:
        return []
    return [*current, *update]


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
    # Query results (libs.contracts.v1.ResultSet, wire form) from db_connector
    # and the tabular tools. A separate channel from the text sources: never
    # ranked, never cut by the reranker (application/result_sets.py).
    result_sets: Annotated[list[dict[str, Any]], merge_result_sets]
    db_error: Optional[str]
    chart_specs: List[dict[str, Any]]           # set bởi visualization_node — 1 phần tử/nguồn nối vào (không gộp chung khác schema)
    chart_error: Optional[str]        # best-effort — KHÔNG bounce về planner/judge_phase1
    rrf_results: List[dict[str, Any]]
    reranked_chunks: List[dict[str, Any]]
    response: str
    # set bởi responder_node — nguồn thực sự được trích dẫn trong response
    citations: List[dict[str, Any]]
    # set bởi responder_node — {"runs": [SQL đã chạy + metric + provenance +
    # rows preview], "clarifications": [...], "warnings": [...]}. Sinh tất định
    # từ result_sets, không do model khai — đi ra SSE qua `__evidence`.
    evidence: dict[str, Any] | None
    suggestions: List[str]
    intent: Optional[str]             # "question" | "task" — set by planner
    # lookup | analytical | comparison | forecast | anomaly | other.
    # Orthogonal to intent — see nodes/model_harness/intent.py.
    analysis_type: Optional[str]
    # Structured request for THIS turn, after merging the follow-up delta
    # into the previous turn (backend/libs/semantic/slots.py).
    slots: Optional[dict[str, Any]]
    # Slots resolved by the PREVIOUS turn of this conversation. Loaded by
    # conversation_runner; the planner merges into it. Absent on turn one.
    previous_slots: Optional[dict[str, Any]]
    # Deterministic underspecification questions raised THIS turn
    # (backend/libs/semantic/ambiguity.py). Non-empty => nothing was executed.
    clarifications: Optional[list[dict[str, Any]]]
    # True when the PREVIOUS turn asked one. Suppresses re-asking, so a
    # question the rules cannot fully resolve does not loop forever.
    previous_clarification: Optional[bool]
    # Reasons already asked about or answered in this line of questioning, from
    # the previous turn's marker. None when that marker predates reasons.
    previous_clarified_reasons: Optional[list[str]]
    # THIS turn's settled reasons, persisted for the next turn.
    clarified_reasons: Optional[list[str]]
    # Default readings applied instead of re-asking ({kind, reason, detail});
    # the responder states them and they ride on evidence.warnings.
    assumptions: Optional[list[dict[str, Any]]]
    # Options the user picked on that clarification ({reason, value}), applied
    # onto the slots by the planner instead of being re-read from the text.
    clarification_answers: Optional[list[dict[str, Any]]]
    language: Optional[str]           # "vi" | "en" — set by planner
    loop_iteration: int               # số lần judge đã chạy (tăng mỗi lần judge FAIL)
    judge_verdict: Optional[str]      # "PASS" | "FAIL"
    judge_error_type: Optional[str]   # "PLANNING_ERROR" | "DATA_QUALITY" | "GROUNDING_ERROR"
    judge_reason: Optional[str]
    error_class: Optional[str]
    error_class_history: List[str]
    attempted_plans: List[str]
    # Kết quả 4 kiểm tra tất định (libs.indicator.verifier) — mỗi kiểm tra một
    # dòng, để UI hiện VerifierBadge và để đo được, thay vì gộp thành một điểm
    # không nói được cái nào đã sai.
    verifier: dict[str, Any] | None
    # Always None since R0-7 (no stand-in plan left to retry); R0-11 routes on it again.
    judge_phase1_error: Optional[str]
    human_approved: Optional[bool]    # None = chưa qua HITL node, True/False = kết quả review
    human_note: Optional[str]         # ghi chú reviewer khi reject
    injected_metadata: Optional[dict[str, Any]]  # set bởi metadata_injector_node — đọc bởi planner + responder
    config: dict[str, Any]
