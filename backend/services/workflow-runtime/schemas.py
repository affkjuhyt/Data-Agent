from typing import Any, List, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


class GuardrailCfg(BaseModel):
    """Cấu hình output-guardrail truyền kèm từ orchestrator (đã cache 60s ở
    agent_catalog.py bên đó) — tránh workflow-runtime phải tự query DB bảng
    Guardrails riêng, giữ 1 nguồn cấu hình duy nhất."""
    guardrail_model: str = "openai/guardrail"
    guardrail_max_tokens: int = 64
    guardrail_timeout: int = 15


class RunRequest(BaseModel):
    query: str
    agent_id: str
    conversation_id: Optional[str] = None
    messages: List[Message] = []
    # True khi được gọi từ orchestrator trong multi-agent mode:
    #   - dùng memory_context từ request thay vì tự fetch
    #   - skip memory write (orchestrator sẽ ghi sau synthesize)
    orchestrated: bool = False
    memory_context: List[Any] = Field(default_factory=list)
    guardrail_cfg: Optional[GuardrailCfg] = None
