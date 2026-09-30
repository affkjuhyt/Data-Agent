import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services.workflow_runtime.api.auth import get_current_user
from libs.shared.auth import require_tenant

from services.workflow_runtime.infrastructure.mongo_client import save_canvas
from services.workflow_runtime.infrastructure.authoring_repository import (
    create_agent,
    delete_agent,
    get_agent,
    list_agents,
    list_workflows,
    publish_agent,
    unpublish_agent,
    update_agent_draft,
)
from services.workflow_runtime.infrastructure.db import ensure_db_available
from services.workflow_runtime.domain.authoring_schemas import CreateAgentRequest, PublishRequest, UpdateDraftRequest
from services.workflow_runtime.infrastructure.authoring_repository import serialize

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/agents", status_code=201)
async def api_create_agent(
    body: CreateAgentRequest,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    try:
        return await create_agent(body.name, body.description or "", tenant_id)
    except Exception as e:
        logger.exception("create_agent failed")
        raise HTTPException(500, str(e))


@router.get("/api/agents")
async def api_list_agents(caller: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    rows = await list_agents(tenant_id)
    return [serialize(row) for row in rows]


@router.get("/api/agents/{agent_id}")
async def api_get_agent(agent_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    agent = await get_agent(agent_id, tenant_id=tenant_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    workflows = await list_workflows(agent_id, tenant_id=tenant_id)
    return {**serialize(agent), "workflows": [serialize(workflow) for workflow in workflows]}


@router.delete("/api/agents/{agent_id}", status_code=204)
async def api_delete_agent(agent_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> None:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    try:
        version_ids = await delete_agent(agent_id, tenant_id=tenant_id)
        for version_id in version_ids:
            await save_canvas(version_id, [], [])
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("delete_agent failed")
        raise HTTPException(500, str(e))


@router.patch("/api/agents/{agent_id}")
async def api_update_agent(agent_id: str, body: UpdateDraftRequest, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    try:
        return await update_agent_draft(agent_id, body.model_dump(exclude_none=True), tenant_id=tenant_id)
    except Exception as e:
        logger.exception("update_agent_draft failed")
        raise HTTPException(500, str(e))


@router.post("/api/agents/{agent_id}/publish")
async def api_publish_agent(agent_id: str, body: PublishRequest, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    try:
        return await publish_agent(agent_id, body.workflow_id, body.kb_connection_id, tenant_id=tenant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_agent failed")
        raise HTTPException(500, str(e))


@router.post("/api/agents/{agent_id}/unpublish")
async def api_unpublish_agent(agent_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    ensure_db_available()
    try:
        return await unpublish_agent(agent_id, tenant_id=tenant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("unpublish_agent failed")
        raise HTTPException(500, str(e))
