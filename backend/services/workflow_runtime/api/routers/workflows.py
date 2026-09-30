import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from libs.shared.auth import require_tenant
from libs.shared.cache_invalidation import publish_graph_invalidation
from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.domain.authoring_schemas import (
    CreateWorkflowRequest,
    PublishVersionRequest,
)
from services.workflow_runtime.infrastructure.authoring_repository import (
    ConflictError,
    create_draft_version,
    create_workflow,
    delete_workflow,
    delete_workflow_version,
    get_agent,
    list_workflow_versions,
    list_workflows,
    publish_workflow_version,
    republish_workflow_version,
    serialize,
    workflow_belongs_to,
    workflow_version_belongs_to,
)
from services.workflow_runtime.infrastructure.config import REDIS_URL
from services.workflow_runtime.infrastructure.db import ensure_db_available
from services.workflow_runtime.infrastructure.mongo_client import clear_canvases

logger = logging.getLogger(__name__)
router = APIRouter()


# Workflows and WorkflowVersions have no tenant column, so RLS cannot scope them:
# ownership is asked of the agent they hang off. Unknown and foreign ids get the
# same 404 so a caller cannot probe which ids exist in another tenant.
async def _require_workflow(workflow_id: str, caller: dict[str, Any]) -> str:
    tenant_id = require_tenant(caller)
    if not await workflow_belongs_to(workflow_id, tenant_id):
        raise HTTPException(404, "Workflow not found")
    return tenant_id


async def _require_version(workflow_version_id: str, caller: dict[str, Any]) -> str:
    tenant_id = require_tenant(caller)
    if not await workflow_version_belongs_to(workflow_version_id, tenant_id):
        raise HTTPException(404, "Workflow version not found")
    return tenant_id


@router.post("/api/agents/{agent_id}/workflows", status_code=201)
async def api_create_workflow(
    agent_id: str,
    body: CreateWorkflowRequest,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_db_available()
    tenant_id = require_tenant(caller)
    agent = await get_agent(agent_id, tenant_id=tenant_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    try:
        return await create_workflow(agent_id, body.name, body.description or "", tenant_id=tenant_id)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logger.exception("create_workflow failed")
        raise HTTPException(500, str(e))


@router.get("/api/agents/{agent_id}/workflows")
async def api_list_workflows(
    agent_id: str, caller: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    ensure_db_available()
    tenant_id = require_tenant(caller)
    if not await get_agent(agent_id, tenant_id=tenant_id):
        raise HTTPException(404, "Agent not found")
    rows = await list_workflows(agent_id, tenant_id=tenant_id)
    return [serialize(row) for row in rows]


@router.get("/api/workflows/{workflow_id}/versions")
async def api_list_workflow_versions(
    workflow_id: str, caller: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    ensure_db_available()
    tenant_id = await _require_workflow(workflow_id, caller)
    rows = await list_workflow_versions(workflow_id, tenant_id=tenant_id)
    return [serialize(row) for row in rows]


@router.post("/api/workflows/{workflow_id}/versions", status_code=201)
async def api_create_draft_version(
    workflow_id: str, caller: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    ensure_db_available()
    tenant_id = await _require_workflow(workflow_id, caller)
    try:
        return await create_draft_version(workflow_id, tenant_id=tenant_id)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_draft_version failed")
        raise HTTPException(500, str(e))


@router.delete("/api/workflow-versions/{version_id}", status_code=204)
async def api_delete_workflow_version(version_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> None:
    ensure_db_available()
    tenant_id = require_tenant(caller)
    try:
        await delete_workflow_version(version_id, tenant_id=tenant_id)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow_version failed")
        raise HTTPException(500, str(e))


@router.delete("/api/workflows/{workflow_id}", status_code=204)
async def api_delete_workflow(workflow_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> None:
    ensure_db_available()
    tenant_id = require_tenant(caller)
    try:
        version_ids = await delete_workflow(workflow_id, tenant_id=tenant_id)
        await clear_canvases(version_ids)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("delete_workflow failed")
        raise HTTPException(500, str(e))


@router.post("/api/workflow-versions/{workflow_version_id}/publish")
async def api_publish_workflow_version(
    workflow_version_id: str,
    body: PublishVersionRequest = PublishVersionRequest(),
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_db_available()
    tenant_id = await _require_version(workflow_version_id, caller)
    try:
        result = await publish_workflow_version(workflow_version_id, body.changelog, tenant_id=tenant_id)
        # New publish gets a new cache key in runtime anyway; broadcast is a
        # belt-and-suspenders nudge so any stale same-id entry is dropped too.
        await publish_graph_invalidation(REDIS_URL, workflow_version_id)
        return result
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("publish_workflow_version failed")
        raise HTTPException(500, str(e))


@router.post("/api/workflow-versions/{workflow_version_id}/republish")
async def api_republish_workflow_version(
    workflow_version_id: str, caller: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    ensure_db_available()
    tenant_id = await _require_version(workflow_version_id, caller)
    try:
        result = await republish_workflow_version(workflow_version_id, tenant_id=tenant_id)
        # Republish reuses the same version id, so the runtime cache key is
        # unchanged — this broadcast is what actually forces the rebuild.
        await publish_graph_invalidation(REDIS_URL, workflow_version_id)
        return result
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("republish_workflow_version failed")
        raise HTTPException(500, str(e))
