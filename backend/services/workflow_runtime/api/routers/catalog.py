"""Builder-facing catalogue routes: agents, node registry, models, prompts.

Tenant resolution: every tenant-scoped route takes the VERIFIED caller from
``get_current_user`` (attached per-route here and again at include time in
app.py) and maps it through ``platform_queries.resolve_tenant`` — the same
pattern as routers/conversations.py. The old ``_resolve_tenant`` read a raw
``X-Tenant-ID`` header, which let any authenticated caller read another
tenant's catalogue by typing a UUID.

/api/llm-providers: backed by the LiteLLM registry (runtime_services/
litellm_proxy.py — the same source routers/llm.py uses for the /models and
/health sub-paths). The hardcoded "Claude Haiku/Sonnet" list is gone; Claude
is not part of this deployment. Model aliases (planner, responder, …) come
from the gateway's own /model/info.
"""
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM, SystemPromptsORM

from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.node_registry import NODE_REGISTRY
from services.workflow_runtime.application.runtime_services import litellm_proxy, platform_queries
from services.workflow_runtime.infrastructure.config import RRF_K

router = APIRouter()

_DEV_AGENT_ID = os.getenv("DEV_AGENT_ID", "00000000-0000-0000-0000-000000000030")

# The alias the runtime answers with — marked is_default for the builder's
# model picker. Matches the "responder" model_name in the LiteLLM config.
_DEFAULT_CHAT_ALIAS = os.getenv("DEFAULT_CHAT_MODEL_ALIAS", "responder")


@router.get("/api/catalog/agents")
async def list_agents(caller: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    tenant_id = platform_queries.resolve_tenant(caller)
    try:
        from libs.database_connector.postgres_connector import client
        if not client.is_connected():
            raise RuntimeError("db not connected")
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (
                await session.execute(
                    select(AgentsORM.id, AgentsORM.name, AgentsORM.description)
                    .where(
                        AgentsORM.tenant_id == tenant_id,
                        AgentsORM.is_active.is_(True),
                        AgentsORM.deleted_at.is_(None),
                        AgentsORM.published_version_id.is_not(None),
                    )
                    .order_by(AgentsORM.created_at.desc())
                )
            ).fetchall()
        return [
            {"id": str(r.id), "name": r.name, "description": r.description or ""}
            for r in rows
        ]
    except Exception as e:
        import logging
        import traceback
        logging.getLogger(__name__).error("catalog/agents failed: %s\n%s", e, traceback.format_exc())
        return [{"id": _DEV_AGENT_ID, "name": "Dev Agent", "description": "", "has_published_version": False}]


@router.get("/api/nodes/registry")
def get_node_registry() -> dict[str, Any]:
    return NODE_REGISTRY


@router.get("/api/runtime-config")
def get_runtime_config() -> dict[str, int]:
    """Runtime defaults the flow builder reads instead of hardcoding them."""
    return {"rrf_k": RRF_K}


@router.get("/api/llm-providers")
async def get_llm_providers(type: Optional[str] = None) -> list[dict[str, Any]]:
    """Model aliases registered on the LiteLLM gateway, in the shape the
    frontend expects ([{id, name, model_id, type, is_default}]). Empty when
    the gateway is unreachable — never an invented list."""
    registry = await litellm_proxy.list_models()
    providers = []
    for m in registry.get("items", []):
        alias = m.get("model_name")
        if not alias:
            continue
        mode = m.get("mode") or "chat"
        providers.append({
            "id": f"llm-{alias}",
            "name": alias,
            "model_id": alias,
            "type": mode,
            "is_default": alias == _DEFAULT_CHAT_ALIAS,
        })
    if type:
        providers = [provider for provider in providers if provider["type"] == type]
    return providers


@router.get("/api/kb-connections")
def get_kb_connections() -> list[Any]:
    return []


@router.get("/api/system-prompts")
async def get_system_prompts(caller: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    """The tenant's SystemPrompts rows. Empty on an empty tenant or an
    unreachable DB — the hardcoded "tenant-dev" sample prompt is gone."""
    tenant_id = platform_queries.resolve_tenant(caller)
    try:
        from libs.database_connector.postgres_connector import client
        if not client.is_connected():
            raise RuntimeError("db not connected")
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (
                await session.execute(
                    select(
                        SystemPromptsORM.id,
                        SystemPromptsORM.tenant_id,
                        SystemPromptsORM.name,
                        SystemPromptsORM.content,
                        SystemPromptsORM.version,
                        SystemPromptsORM.is_active,
                    )
                    .where(SystemPromptsORM.tenant_id == tenant_id)
                    .order_by(SystemPromptsORM.inserted_at.desc())
                )
            ).fetchall()
        return [
            {
                "id": str(r.id),
                "tenant_id": str(r.tenant_id),
                "name": r.name,
                "content": r.content,
                "version": r.version,
                "is_active": r.is_active,
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001 — an empty list is the answer, not an error
        import logging
        logging.getLogger(__name__).error("catalog/system-prompts failed: %s", e)
        return []
