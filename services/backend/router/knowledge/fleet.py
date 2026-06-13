"""/api/fleet/stats — FleetOverview dashboard stats."""
from fastapi import APIRouter, Depends, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.backend.dependencies.ui_context import get_ui_context, svc
from services.backend.pipeline.kb_ui_operation.fleet_ops import get_fleet_stats

router = APIRouter(prefix="/api/fleet", tags=["Fleet"])


@router.get("/stats", response_model=ResponseModel)
async def fleet_stats(request: Request, ctx: dict = Depends(get_ui_context)):
    result = await get_fleet_stats(
        postgres=svc(request, "postgres"),
        qdrant=svc(request, "qdrant"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)
