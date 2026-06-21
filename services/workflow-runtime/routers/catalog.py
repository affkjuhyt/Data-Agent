import os
from typing import Optional

from fastapi import APIRouter, Request
from sqlalchemy import text

from config import RRF_K
from node_registry import NODE_REGISTRY

router = APIRouter()

_DEV_AGENT_ID  = os.getenv("DEV_AGENT_ID",  "00000000-0000-0000-0000-000000000030")
_DEV_TENANT_ID = os.getenv("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")

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


def _resolve_tenant(header: str | None) -> str:
    """Return header if it looks like a UUID, otherwise fall back to dev tenant."""
    if header:
        try:
            from uuid import UUID
            UUID(header)
            return header
        except ValueError:
            pass
    return _DEV_TENANT_ID


@router.get("/api/catalog/agents")
async def list_agents(request: Request):
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    try:
        from services.database_connector.postgres_connector import client
        if not client.is_connected():
            raise RuntimeError("db not connected")
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (await session.execute(text("""
                SELECT a.id, a.name, a.description
                FROM "Agents" a
                WHERE a.tenant_id            = :tenant_id
                  AND a.is_active            = true
                  AND a.deleted_at           IS NULL
                  AND a.published_version_id IS NOT NULL
                ORDER BY a.created_at DESC
            """), {"tenant_id": tenant_id})).fetchall()
        return [
            {"id": str(r.id), "name": r.name, "description": r.description or ""}
            for r in rows
        ]
    except Exception as e:
        import logging, traceback
        logging.getLogger(__name__).error("catalog/agents failed: %s\n%s", e, traceback.format_exc())
        return [{"id": _DEV_AGENT_ID, "name": "Dev Agent", "description": "", "has_published_version": False}]


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
