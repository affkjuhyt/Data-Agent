import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.database_connector.mongo_connector import client as mongo_client

logger = logging.getLogger(__name__)

router = APIRouter()


class CanvasPayload(BaseModel):
    nodes: list
    edges: list


@router.post("/workflow-versions/{workflow_version_id}/canvas")
async def api_save_canvas(workflow_version_id: str, body: CanvasPayload):
    try:
        await mongo_client.save_canvas(workflow_version_id, body.nodes, body.edges)
        return {"saved": True, "workflow_version_id": workflow_version_id}
    except Exception as e:
        logger.exception("save_canvas failed")
        raise HTTPException(500, str(e))


@router.get("/workflow-versions/{workflow_version_id}/canvas")
async def api_load_canvas(workflow_version_id: str):
    try:
        return await mongo_client.load_canvas(workflow_version_id)
    except Exception as e:
        logger.exception("load_canvas failed")
        raise HTTPException(500, str(e))
