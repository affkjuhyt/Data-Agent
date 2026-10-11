from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: str
    content: str


class GuardrailCfg(BaseModel):
    """Cấu hình output-guardrail truyền kèm từ orchestrator (đã cache 60s ở
    agent_catalog.py bên đó) — tránh workflow_runtime phải tự query DB bảng
    Guardrails riêng, giữ 1 nguồn cấu hình duy nhất."""
    guardrail_model: str = "openai/guardrail"
    guardrail_max_tokens: int = 64
    guardrail_timeout: int = 15


class ClarificationAnswer(BaseModel):
    """One option picked on a clarification card: the `reason` of the question
    it answers (libs.semantic.ambiguity.Reason) and the option's slot value."""
    reason: str = Field(max_length=64)
    value: str = Field(max_length=200)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    agent_id: str
    conversation_id: Optional[str] = None
    messages: List[Message] = []
    # Structured answers to the previous turn's clarification. Applied straight
    # onto the slots by the planner (ambiguity.apply_answers), not re-read from
    # the query text.
    clarification_answers: List[ClarificationAnswer] = Field(default_factory=list, max_length=8)


class InternalRunRequest(RunRequest):
    """Additional fields accepted only on the verified orchestrator path."""

    orchestrated: bool = False
    memory_context: List[Any] = Field(default_factory=list)
    guardrail_cfg: Optional[GuardrailCfg] = None
