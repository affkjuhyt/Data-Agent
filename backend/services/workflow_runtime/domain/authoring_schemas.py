from typing import Any, Optional

from pydantic import BaseModel


class CreateAgentRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class CanvasPayload(BaseModel):
    nodes: list[Any]
    edges: list[Any]


class PublishRequest(BaseModel):
    workflow_id: str
    changelog: Optional[str] = None
    kb_connection_id: Optional[str] = None


class PublishVersionRequest(BaseModel):
    changelog: Optional[str] = None


class UpdateDraftRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # LiteLLM alias, as GET /api/llm-providers lists it in model_id.
    responder_model: Optional[str] = None
    system_prompt_id: Optional[str] = None
    guardrail_id: Optional[str] = None
    memory_enabled: Optional[bool] = None
