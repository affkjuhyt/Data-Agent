from fastapi import Depends, Header, HTTPException


def get_current_user(
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_user_roles: str = Header(..., alias="X-User-Roles"),
    x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
) -> dict:
    if x_kong_verified != "true":
        raise HTTPException(status_code=401, detail="Request not verified by Kong gateway")
    return {
        "user_id": x_user_id,
        "tenant_id": x_tenant_id,
        "roles": x_user_roles.split(","),
    }


def require_role(*roles: str):
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if not any(r in user["roles"] for r in roles):
            raise HTTPException(status_code=403, detail=f"Required role: {' or '.join(roles)}")
        return user
    return _dep
