"""Authentication & authorization for KB routes — the single source of truth.

Identity model
--------------
The Kong `aeroflow-jwks` plugin validates the bearer JWT, STRIPS any
client-supplied identity headers, and injects verified ones:

    X-Kong-Verified: true
    X-User-Id, X-Tenant-Id, X-User-Roles (comma-joined), X-Role-Id

`authenticate` is the ONE FastAPI dependency every route uses to extract that
identity. There is no dev/mock bypass: a request that did not pass through Kong
(no `X-Kong-Verified: true`) is rejected with 401 — everything goes through the
gateway, always.

`require_perm` builds on `authenticate` to enforce role-based permissions
server-side (the UI gates the same actions for UX, but this is authoritative —
the UI map is bypassable by calling the API directly).
"""
import re

from fastapi import Depends, Header, HTTPException

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)

# Keycloak realm role names → app canonical role literals (match
# src/lib/permissions.ts and ingestion.DEV_ROLE_PERMISSION_IDS keys).
_KC_ROLE_MAP = {
    "platform-admin":   "PLATFORM_ADMIN",
    "ai-engineer":      "AI_ENGINEER",
    "executive-viewer": "EXECUTIVE",
}

# Keep in sync with src/lib/permissions.ts → ROLE_PERMISSIONS.
_ALL = {
    "delete_data", "edit_conflict", "process_layer", "add_data", "add_warehouse",
    "add_filtering_policy", "edit_filtering_policy", "delete_filtering_policy",
    "edit_extraction_policy", "toggle_qdrant", "add_warehouse_config",
    "edit_warehouse_config", "add_chunk_version",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "PLATFORM_ADMIN": set(_ALL),
    "AI_ENGINEER":    {"toggle_qdrant", "edit_extraction_policy"},
    "EXECUTIVE":      {"edit_conflict", "process_layer", "add_filtering_policy"},
}


def _canon_role(raw: str) -> str:
    raw = (raw or "").strip()
    return _KC_ROLE_MAP.get(raw.lower(), raw.upper().replace("-", "_"))


def authenticate(
    x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
    x_user_id:       str = Header(default="", alias="X-User-Id"),
    x_user_name:     str = Header(default="", alias="X-User-Name"),
    x_tenant_id:     str = Header(default="", alias="X-Tenant-Id"),
    x_user_roles:    str = Header(default="", alias="X-User-Roles"),
    x_role_id:       str = Header(default="", alias="X-Role-Id"),
) -> dict:
    """Resolve {role, roles, tenant_id, user_id, user_name} from the Kong-verified headers.

    Fail-closed: anything not stamped `X-Kong-Verified: true` by the gateway is
    rejected. This is the only identity extractor in the service.

    `user_name` is the human-readable login (Keycloak `preferred_username`, injected
    by Kong as X-User-Name) — use it for display attribution like added_by/created_by
    so the UI shows "helios-admin" instead of the opaque `user_id` UUID. Falls back to
    user_id if the gateway did not provide a name.
    """
    if x_kong_verified != "true":
        raise HTTPException(status_code=401, detail="Request not verified by Kong gateway")

    raw_roles = [r for r in x_user_roles.split(",") if r.strip()]
    if not raw_roles and x_role_id:
        raw_roles = [x_role_id]
    roles = [_canon_role(r) for r in raw_roles]

    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id or not _UUID_RE.match(tenant_id):
        raise HTTPException(status_code=400, detail="Kong-verified request missing valid tenant_id")

    return {
        "role": roles[0] if roles else "",
        "roles": roles,
        "tenant_id": tenant_id,
        "user_id": x_user_id or "",
        "user_name": (x_user_name or "").strip() or (x_user_id or ""),
    }


def require_perm(*perms: str):
    """Dependency factory: 403 unless the caller's role grants any of `perms`."""
    def _dep(ctx: dict = Depends(authenticate)) -> dict:
        granted = ROLE_PERMISSIONS.get(ctx["role"], set())
        if not any(p in granted for p in perms):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx['role'] or 'unknown'}' lacks permission: {' or '.join(perms)}",
            )
        return ctx
    return _dep
