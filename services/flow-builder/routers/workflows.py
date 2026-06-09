import logging

from fastapi import APIRouter, Header, HTTPException

from config import DEV_TENANT_ID
from db_mongo import save_canvas
from db_pg import (
    create_draft_version,
    create_workflow,
    delete_workflow,
    delete_workflow_version,
    get_agent,
    list_workflow_versions,
    list_workflows,
    publish_workflow_version,
    republish_workflow_version,
)
from dependencies import ensure_db_available
from schemas import CreateWorkflowRequest, PublishVersionRequest
from utils import serialize

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/agents/{agent_id}/workflows", status_code=201)
async def api_create_workflow(agent_id: str, body: CreateWorkflowRequest):
    ensure_db_available()
    agent = await get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    try:
        return await create_workflow(agent_id, body.name, body.description or "")
    except Exception as e:
        logger.exception("create_workflow failed")
        raise HTTPException(500, str(e))


@router.get("/api/agents/{agent_id}/workflows")
async def api_list_workflows(agent_id: str):
    ensure_db_available()
    rows = await list_workflows(agent_id)
    return [serialize(row) for row in rows]


@router.get("/api/workflows/{workflow_id}/versions")
async def api_list_workflow_versions(workflow_id: str):
    ensure_db_available()
    rows = await list_workflow_versions(workflow_id)
    return [serialize(row) for row in rows]


@router.post("/api/workflows/{workflow_id}/versions", status_code=201)
async def api_create_draft_version(workflow_id: str):
    ensure_db_available()
    try:
        return await create_draft_version(workflow_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_draft_version failed")
        raise HTTPException(500, str(e))


@router.delete("/api/workflow-versions/{version_id}", status_code=204)
async def api_delete_workflow_version(version_id: str, x_tenant_id: str = Header(default=None)):
    ensure_db_available()
    tenant_id = x_tenant_id or DEV_TENANT_ID
    try:
        await delete_workflow_version(version_id, tenant_id=tenant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow_version failed")
        raise HTTPException(500, str(e))


@router.delete("/api/workflows/{workflow_id}", status_code=204)
async def api_delete_workflow(workflow_id: str, x_tenant_id: str = Header(default=None)):
    ensure_db_available()
    tenant_id = x_tenant_id or DEV_TENANT_ID
    try:
        version_ids = await delete_workflow(workflow_id, tenant_id=tenant_id)
        for version_id in version_ids:
            await save_canvas(version_id, [], [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow failed")
        raise HTTPException(500, str(e))


@router.post("/api/workflow-versions/{workflow_version_id}/publish")
async def api_publish_workflow_version(
    workflow_version_id: str,
    body: PublishVersionRequest = PublishVersionRequest(),
):
    ensure_db_available()
    try:
        return await publish_workflow_version(workflow_version_id, body.changelog)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_workflow_version failed")
        raise HTTPException(500, str(e))


@router.post("/api/workflow-versions/{workflow_version_id}/republish")
async def api_republish_workflow_version(workflow_version_id: str):
    ensure_db_available()
    try:
        return await republish_workflow_version(workflow_version_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("republish_workflow_version failed")
        raise HTTPException(500, str(e))
