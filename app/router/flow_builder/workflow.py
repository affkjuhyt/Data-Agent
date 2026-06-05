import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.database_connector.postgres_connector import client

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = ""


class PublishVersionRequest(BaseModel):
    changelog: Optional[str] = None


@router.post("/agents/{agent_id}/workflows", status_code=201)
async def api_create_workflow(agent_id: str, body: CreateWorkflowRequest):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    agent = await client.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    try:
        result = await client.create_workflow(agent_id, body.name, body.description or "")
        return result
    except Exception as e:
        logger.exception("create_workflow failed")
        raise HTTPException(500, str(e))


@router.get("/agents/{agent_id}/workflows")
async def api_list_workflows(agent_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    rows = await client.list_workflows(agent_id)
    return [_serialize(r) for r in rows]


@router.get("/workflows/{workflow_id}/versions")
async def api_list_workflow_versions(workflow_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    rows = await client.list_workflow_versions(workflow_id)
    return [_serialize(r) for r in rows]


@router.post("/workflows/{workflow_id}/versions", status_code=201)
async def api_create_draft_version(workflow_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.create_draft_version(workflow_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_draft_version failed")
        raise HTTPException(500, str(e))


@router.delete("/workflow-versions/{version_id}", status_code=204)
async def api_delete_workflow_version(version_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        await client.delete_workflow_version(version_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow_version failed")
        raise HTTPException(500, str(e))


@router.delete("/workflows/{workflow_id}", status_code=204)
async def api_delete_workflow(workflow_id: str):
    from services.database_connector.mongo_connector import client as mongo_client
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        version_ids = await client.delete_workflow(workflow_id)
        for vid in version_ids:
            await mongo_client.save_canvas(vid, [], [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow failed")
        raise HTTPException(500, str(e))


@router.post("/workflow-versions/{workflow_version_id}/publish")
async def api_publish_workflow_version(workflow_version_id: str, body: PublishVersionRequest = PublishVersionRequest()):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.publish_workflow_version(workflow_version_id, body.changelog)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_workflow_version failed")
        raise HTTPException(500, str(e))


@router.post("/workflow-versions/{workflow_version_id}/republish")
async def api_republish_workflow_version(workflow_version_id: str):
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
    try:
        result = await client.republish_workflow_version(workflow_version_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("republish_workflow_version failed")
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
