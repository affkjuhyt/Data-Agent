"""/api/fleet/stats — FleetOverview dashboard stats."""
from fastapi import APIRouter, Depends, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.pipeline.kb_ui_operation.fleet_ops import get_fleet_stats

router = APIRouter(prefix="/api/fleet", tags=["Fleet"])


@router.get("/stats", response_model=ResponseModel)
async def fleet_stats(request: Request, ctx: dict = Depends(authenticate)):
    result = await get_fleet_stats(
        postgres=svc(request, "postgres"),
        qdrant=svc(request, "qdrant"),
        neo4j=svc(request, "neo4j"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)
