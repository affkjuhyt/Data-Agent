from typing import Any, Optional
from pydantic import BaseModel, Field


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
    messages: list[dict] = Field(default_factory=list)


class TaskSchema(BaseModel):
    title: str
    agent_id: str = ""
    depend_on: list[int | str] = Field(default_factory=list)


class TargetSchema(BaseModel):
    node_id: str = ""
    description: str
    tasks: list[TaskSchema] = Field(default_factory=list)


class PlanSchema(BaseModel):
    plan_id: str
    user_input: str
    targets: list[TargetSchema] = Field(default_factory=list)
    confidence: float = 0.0


class TaskResult(BaseModel):
    task_index: int
    task_title: str
    agent_id: str
    agent_name: str = ""
    output: str
    success: bool
    error: Optional[str] = None
    conversation_id: Optional[str] = None  # để frontend fetch LangFuse trace nội bộ
    lf_trace_id: Optional[str] = None      # orchestrator trace id (orchestrated mode)
    lf_span_id: Optional[str] = None       # task span id trong orchestrator trace
    start_ms: int = 0    # relative to executor start
    latency_ms: int = 0


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
    synthesizer_ms: int = 0
    session_id: str = ""


class ClarificationResume(BaseModel):
    session_id: str   # dùng làm thread_id để resume LangGraph
    clarification: str
