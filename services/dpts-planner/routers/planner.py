import logging

from fastapi import APIRouter, HTTPException

import service
from schemas import CreatePlanRequest, NodeFailedRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/plans", status_code=201)
async def api_create_plan(body: CreatePlanRequest):
    # user query -> Cached Plan / Planning Process -> Plan -> plan DB
    try:
        result = await service.create_plan(body.user_id, body.user_input, use_cache=body.use_cache)
    except Exception as e:
        logger.exception("create_plan failed")
        raise HTTPException(500, str(e))
    if result["plan"] is None:
        raise HTTPException(422, "no plan could be formulated for this input")
    return result


@router.get("/api/users/{user_id}/plans/{plan_id}")
async def api_get_plan(user_id: str, plan_id: str):
    # executor reads the ready plan here (by user_id + plan_id)
    plan = await service.get_plan(user_id, plan_id)
    if plan is None:
        raise HTTPException(404, "plan not found")
    return plan


@router.post("/api/users/{user_id}/plans/{plan_id}/node-failed")
async def api_node_failed(user_id: str, plan_id: str, body: NodeFailedRequest):
    # executor reports a broken node -> Planner gate -> trim + expand again
    try:
        result = await service.report_node_failure(user_id, plan_id, body.node_id)
    except Exception as e:
        logger.exception("node_failed handling failed")
        raise HTTPException(500, str(e))
    return result
