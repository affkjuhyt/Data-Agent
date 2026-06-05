import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.database_connector.postgres_connector import client
from services.database_connector.mongo_connector import client as mongo_client

logger = logging.getLogger(__name__)

router = APIRouter()

_DEV_TENANT_ID = os.environ.get("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")


class CreateAgentRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class UpdateDraftRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    responder_model_id: Optional[str] = None
    system_prompt_id: Optional[str] = None
    guardrail_id: Optional[str] = None
    memory_enabled: Optional[bool] = None


@router.post("/agents", status_code=201)
async def api_create_agent(
    body: CreateAgentRequest,
    x_tenant_id: str = Header(default=None),
):
    tenant_id = x_tenant_id or _DEV_TENANT_ID
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.create_agent(body.name, body.description or "", tenant_id)
        return result
    except Exception as e:
        logger.exception("create_agent failed")
        raise HTTPException(500, str(e))


@router.get("/agents")
async def api_list_agents(x_tenant_id: str = Header(default=None)):
    tenant_id = x_tenant_id or _DEV_TENANT_ID
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    rows = await client.list_agents(tenant_id)
    return [_serialize(r) for r in rows]


@router.get("/agents/{agent_id}")
async def api_get_agent(agent_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    agent = await client.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    workflows = await client.list_workflows(agent_id)
    return {**_serialize(agent), "workflows": [_serialize(w) for w in workflows]}


@router.patch("/agents/{agent_id}")
async def api_update_agent(agent_id: str, body: UpdateDraftRequest):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.update_agent_draft(agent_id, body.model_dump(exclude_none=True))
        return result
    except Exception as e:
        logger.exception("update_agent_draft failed")
        raise HTTPException(500, str(e))


@router.delete("/agents/{agent_id}", status_code=204)
async def api_delete_agent(agent_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        version_ids = await client.delete_agent(agent_id)
        for vid in version_ids:
            await mongo_client.save_canvas(vid, [], [])
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("delete_agent failed")
        raise HTTPException(500, str(e))


class PublishRequest(BaseModel):
    workflow_id: str


@router.post("/agents/{agent_id}/publish")
async def api_publish_agent(agent_id: str, body: PublishRequest):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.publish_agent(agent_id, body.workflow_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_agent failed")
        raise HTTPException(500, str(e))


def _serialize(row: dict) -> dict:
    import uuid
    from datetime import datetime
    out = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
