from typing import Optional

from fastapi import APIRouter, Request

from config import RRF_K
from node_registry import NODE_REGISTRY

router = APIRouter()

LLM_PROVIDERS = [
    {"id": "llm-planner", "name": "Claude Haiku", "model_id": "planner", "type": "chat", "is_default": False},
    {"id": "llm-responder", "name": "Claude Sonnet", "model_id": "responder", "type": "chat", "is_default": True},
]

SYSTEM_PROMPTS = [
    {
        "id": "sp-default",
        "name": "Default",
        "tenant_id": "tenant-dev",
        "content": "You are a helpful assistant for GTEL platform.",
    },
]


@router.get("/api/nodes/registry")
def get_node_registry():
    return NODE_REGISTRY


@router.get("/api/runtime-config")
def get_runtime_config():
    """Runtime defaults the flow builder reads instead of hardcoding them."""
    return {"rrf_k": RRF_K}


@router.get("/api/llm-providers")
def get_llm_providers(type: Optional[str] = None):
    providers = LLM_PROVIDERS
    if type:
        providers = [provider for provider in providers if provider["type"] == type]
    return providers


@router.get("/api/kb-connections")
def get_kb_connections(request: Request):
    tenant_id = request.headers.get("X-Tenant-ID", "tenant-dev")
    return []


@router.get("/api/system-prompts")
def get_system_prompts(request: Request):
    tenant_id = request.headers.get("X-Tenant-ID", "tenant-dev")
    return [prompt for prompt in SYSTEM_PROMPTS if prompt["tenant_id"] == tenant_id]
