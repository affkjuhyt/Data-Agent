from typing import Optional

from pydantic import BaseModel


class CreateAgentRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class CanvasPayload(BaseModel):
    nodes: list
    edges: list


class PublishRequest(BaseModel):
    workflow_id: str
    changelog: Optional[str] = None
    kb_connection_id: Optional[str] = None


class PublishVersionRequest(BaseModel):
    changelog: Optional[str] = None


class UpdateDraftRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    responder_model_id: Optional[str] = None
    system_prompt_id: Optional[str] = None
    guardrail_id: Optional[str] = None
    memory_enabled: Optional[bool] = None
