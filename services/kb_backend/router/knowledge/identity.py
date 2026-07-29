from typing import Any
from fastapi import APIRouter, Depends

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate, RBAC

router = APIRouter(prefix="/api/knowledge", tags=["Identity"])


@router.get("/whoami", response_model=ResponseModel)
async def whoami(ctx: dict[str, Any] = Depends(authenticate)) -> ResponseModel[Any]:
    permissions = RBAC.permissions_for(ctx["role"])
    return ResponseModel(code=200, data={**ctx, "permissions": permissions})
