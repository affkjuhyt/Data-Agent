from typing import Any, List, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


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
