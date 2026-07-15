import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from db_mongo import load_canvas, save_canvas
from schemas import CanvasPayload

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/workflow-versions/{workflow_version_id}/canvas")
async def api_save_canvas(workflow_version_id: str, body: CanvasPayload) -> dict[str, Any]:
    try:
        await save_canvas(workflow_version_id, body.nodes, body.edges)
        return {"saved": True, "workflow_version_id": workflow_version_id}
    except Exception as e:
        logger.exception("save_canvas failed")
        raise HTTPException(500, str(e))


@router.get("/api/workflow-versions/{workflow_version_id}/canvas")
async def api_load_canvas(workflow_version_id: str) -> dict[str, Any]:
    try:
        return await load_canvas(workflow_version_id)
    except Exception as e:
        logger.exception("load_canvas failed")
        raise HTTPException(500, str(e))
