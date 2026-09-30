import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services.workflow_runtime.auth import get_current_user
from services.workflow_runtime.mongo_client import load_canvas, save_canvas
from services.workflow_runtime.authoring_repository import workflow_version_belongs_to
from libs.shared.auth import require_tenant
from services.workflow_runtime.authoring_schemas import CanvasPayload

logger = logging.getLogger(__name__)
router = APIRouter()


async def _owned_version(workflow_version_id: str, caller: dict[str, Any]) -> str:
    """Confirm the caller's tenant owns this workflow version, or 404.

    These two routes are keyed by workflow_version_id alone and read/write
    Mongo, which has no row-level security to fall back on — so unlike the
    Postgres-backed routes, authentication alone leaves them cross-tenant: any
    authenticated user could load or overwrite any tenant's canvas given an id.
    A canvas defines what an agent does, so overwriting one is a change to
    another tenant's running agent.

    404, not 403, for a version owned by someone else: 403 would confirm that
    the id exists, which is itself a fact about another tenant.
    """
    tenant_id = require_tenant(caller)
    if not await workflow_version_belongs_to(workflow_version_id, tenant_id):
        logger.warning(
            "canvas | tenant=%s attempted access to workflow_version=%s it does not own",
            tenant_id, workflow_version_id,
        )
        raise HTTPException(404, "workflow version not found")
    return tenant_id


@router.post("/api/workflow-versions/{workflow_version_id}/canvas")
async def api_save_canvas(
    workflow_version_id: str,
    body: CanvasPayload,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    await _owned_version(workflow_version_id, caller)
    try:
        await save_canvas(workflow_version_id, body.nodes, body.edges)
        return {"saved": True, "workflow_version_id": workflow_version_id}
    except Exception as e:
        logger.exception("save_canvas failed")
        raise HTTPException(500, str(e))


@router.get("/api/workflow-versions/{workflow_version_id}/canvas")
async def api_load_canvas(
    workflow_version_id: str,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    await _owned_version(workflow_version_id, caller)
    try:
        return await load_canvas(workflow_version_id)
    except Exception as e:
        logger.exception("load_canvas failed")
        raise HTTPException(500, str(e))
