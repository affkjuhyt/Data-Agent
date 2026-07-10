"""/api/knowledge/whoami — identity-chain echo.

Returns exactly what the router resolved for the current request: the
Kong-verified user_id / tenant_id / role(s) / role_id plus the permission set
RBAC grants that role. Touches no datastore, so it works in KB_LITE mode and is
the simplest way to verify the gateway→router identity chain end-to-end:

  - No token / not via Kong  → 401 (authenticate fail-closed)
  - Verified by Kong          → 200 with the resolved identity + permissions

The 200 body is the proof the JWT claims (sub, tenant_id, role_id, realm roles)
propagated through Kong's header injection into the backend.
"""
from fastapi import APIRouter, Depends

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate, RBAC

router = APIRouter(prefix="/api/knowledge", tags=["Identity"])


@router.get("/whoami", response_model=ResponseModel)
async def whoami(ctx: dict = Depends(authenticate)):
    granted = sorted(RBAC.permissions_for(ctx["role"]))
    return ResponseModel(code=200, data={**ctx, "permissions": granted})
