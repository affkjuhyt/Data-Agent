from typing import Any
from fastapi import APIRouter, Depends, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate, postgres_for
from services.kb_backend.dependencies.permissions import PermissionStoreUnavailable, store

router = APIRouter(prefix="/api/knowledge", tags=["Identity"])


@router.get("/whoami", response_model=ResponseModel)
async def whoami(
        request: Request, ctx: dict[str, Any] = Depends(authenticate),
) -> ResponseModel[Any]:
    try:
        permissions = await store.permissions_for(postgres_for(request), ctx["role"])
    except PermissionStoreUnavailable:
        # Identity is known even when the matrix is not. Returning it with no
        # permissions would read as "you may do nothing", which is a different
        # claim from "we could not find out" — so the absence is explicit.
        return ResponseModel(
            code=200, data={**ctx, "permissions": None, "permissions_available": False},
        )
    return ResponseModel(
        code=200, data={**ctx, "permissions": permissions, "permissions_available": True},
    )
