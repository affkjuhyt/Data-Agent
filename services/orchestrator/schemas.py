from typing import Any, Optional
from pydantic import BaseModel, Field


class OrchestrateRequest(BaseModel):
    query: str
    user_id: str = "default"
    session_id: Optional[str] = None
    use_cache: bool = True


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


class TargetResult(BaseModel):
    node_id: str
    description: str
    task_results: list[TaskResult] = Field(default_factory=list)
    success: bool = True
    failed_task: Optional[str] = None


class OrchestrateResponse(BaseModel):
    plan_id: str
    response: str
    target_results: list[TargetResult] = Field(default_factory=list)
    recovered: bool = False
