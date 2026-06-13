"""Shared FastAPI dependencies for UI-facing routes."""
import re
from fastapi import Header, HTTPException, Request

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)
_DEV_TENANT_UUID = "00000000-0000-0000-0000-000000000001"


def get_ui_context(
    x_role:      str = Header(default="AI_ENGINEER", alias="X-Role"),
    x_tenant_id: str = Header(default="",            alias="X-Tenant-Id"),
    x_user_id:   str = Header(default="dev-user",    alias="X-User-Id"),
) -> dict:
    """
    Lenient context for UI-facing routes.
    In production Kong injects X-Tenant-Id / X-User-Id.
    In development the UI only sends X-Role — defaults are used for the rest.
    Non-UUID tenant values (e.g. 'demo') are coerced to the dev default UUID.
    """
    tid = x_tenant_id.strip() if x_tenant_id else ""
    if not tid or not _UUID_RE.match(tid):
        tid = _DEV_TENANT_UUID
    return {"role": x_role, "tenant_id": tid, "user_id": x_user_id}


def svc(request: Request, name: str):
    obj = getattr(request.app.state, name, None)
    if obj is None:
        raise HTTPException(status_code=503, detail=f"Service '{name}' not initialised")
    return obj
