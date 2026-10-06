from typing import Any, Optional

from pydantic import BaseModel, Field


class ClarificationAnswer(BaseModel):
    """An option picked on the agent's clarification card (mirrors
    workflow_runtime.schemas.ClarificationAnswer)."""
    reason: str = Field(max_length=64)
    value: str = Field(max_length=200)


class OrchestrateRequest(BaseModel):
    query: str
    user_id: str = "default"
    session_id: Optional[str] = None
    use_cache: bool = True
    # Nếu có agent_id → single agent mode (skip ToT, gọi thẳng agent đó)
    # Nếu không → multi agent mode (ToT planner tự phân chia)
    agent_id: Optional[str] = None
    # Khi frontend nhận __clarification event, user trả lời rồi gửi lại request với field này
    clarification_response: Optional[str] = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    # Single-agent mode: the agent conversation (from __conv_ids) this turn
    # continues. Its slots and "just asked" marker live there — without it
    # every turn starts a fresh thread and an answer looks like turn one.
    conversation_id: Optional[str] = None
    # Options picked on a clarification card, relayed to the agent as is.
    clarification_answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=8)


class TaskResult(BaseModel):
    task_index: int
    task_title: str
    agent_id: str
    agent_name: str = ""
    output: str
    success: bool
    error: Optional[str] = None
    conversation_id: Optional[str] = None  # frontend dùng để fetch Langfuse trace
    start_ms: int = 0
    latency_ms: int = 0
    citations: list[dict[str, Any]] = Field(default_factory=list)


class TargetResult(BaseModel):
    node_id: str
    description: str
    task_results: list[TaskResult] = Field(default_factory=list)
    success: bool = True
    failed_task: Optional[str] = None


class OrchestrateResponse(BaseModel):
    plan_id: str
    response: str
    success: bool = True
    # HITL clarification — nếu True, frontend hiển thị questions và gọi /resume
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    target_results: list[TargetResult] = Field(default_factory=list)
    recovered: bool = False
    suggestions: list[str] = Field(default_factory=list)
    planner_ms: int = 0
    executor_ms: int = 0
    summary_ms: int = 0
    session_id: str = ""


class ClarificationResume(BaseModel):
    session_id: str   # dùng làm thread_id để resume LangGraph
    clarification: str
