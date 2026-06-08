import logging

from fastapi import APIRouter, Header, HTTPException

from config import DEV_TENANT_ID
from db_mongo import save_canvas
from db_pg import (
    create_agent,
    delete_agent,
    get_agent,
    list_agents,
    list_workflows,
    publish_agent,
    unpublish_agent,
    update_agent_draft,
)
from dependencies import ensure_db_available
from schemas import CreateAgentRequest, PublishRequest, UpdateDraftRequest
from utils import serialize

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/agents", status_code=201)
async def api_create_agent(
    body: CreateAgentRequest,
    x_tenant_id: str = Header(default=None),
):
    tenant_id = x_tenant_id or DEV_TENANT_ID
    ensure_db_available()
    try:
        return await create_agent(body.name, body.description or "", tenant_id)
    except Exception as e:
        logger.exception("create_agent failed")
        raise HTTPException(500, str(e))


@router.get("/api/agents")
async def api_list_agents(x_tenant_id: str = Header(default=None)):
    tenant_id = x_tenant_id or DEV_TENANT_ID
    ensure_db_available()
    rows = await list_agents(tenant_id)
    return [serialize(row) for row in rows]


@router.get("/api/agents/{agent_id}")
async def api_get_agent(agent_id: str):
    ensure_db_available()
    agent = await get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    workflows = await list_workflows(agent_id)
    return {**serialize(agent), "workflows": [serialize(workflow) for workflow in workflows]}


@router.delete("/api/agents/{agent_id}", status_code=204)
async def api_delete_agent(agent_id: str):
    ensure_db_available()
    try:
        version_ids = await delete_agent(agent_id)
        for version_id in version_ids:
            await save_canvas(version_id, [], [])
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("delete_agent failed")
        raise HTTPException(500, str(e))


@router.patch("/api/agents/{agent_id}")
async def api_update_agent(agent_id: str, body: UpdateDraftRequest):
    ensure_db_available()
    try:
        return await update_agent_draft(agent_id, body.model_dump(exclude_none=True))
    except Exception as e:
        logger.exception("update_agent_draft failed")
        raise HTTPException(500, str(e))


@router.post("/api/agents/{agent_id}/publish")
async def api_publish_agent(agent_id: str, body: PublishRequest):
    ensure_db_available()
    try:
        return await publish_agent(agent_id, body.workflow_id, body.kb_connection_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_agent failed")
        raise HTTPException(500, str(e))


@router.post("/api/agents/{agent_id}/unpublish")
async def api_unpublish_agent(agent_id: str):
    ensure_db_available()
    try:
        return await unpublish_agent(agent_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("unpublish_agent failed")
        raise HTTPException(500, str(e))
