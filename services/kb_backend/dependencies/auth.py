import re
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException

from services.kb_backend.config import resolve_role_id

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


UNIVERSAL_PERMISSIONS: dict[str, bool] = {
    "viewDocuments": False, "editTableRow": False,
    "viewConflicts": False,
    "viewFleetStats": False,
    "viewKnowledgeBases": False,
    "retrieveKnowledge": False,
    "viewGraph": False, "runGraphQuery": False,
    "viewPolicies": False,
    "viewQdrantCollections": False, "searchQdrant": False,
    "viewTrash": False,
    "viewWarehouses": False,
}

RESTRICTED_PERMISSIONS: dict[str, bool] = {
    "addData": False,
    "processLayer": False,
    "deleteData": False,
    "addChunkVersion": False,
    "editConflict": False,
    "addFilteringPolicy": False,
    "editFilteringPolicy": False,
    "deleteFilteringPolicy": False,
    "editExtractionPolicy": False,
    "toggleQdrant": False,
    "addWarehouse": False,
    "addWarehouseConfig": False,
    "editWarehouseConfig": False,
}


def blank_permission_grants() -> dict[str, bool]:
    return {**UNIVERSAL_PERMISSIONS, **RESTRICTED_PERMISSIONS}


class PermissionRegistry:
    __slots__ = ("_grants", "_aliases")

    def __init__(self) -> None:
        self._grants: dict[str, dict[str, bool]] = {}
        self._aliases: dict[str, str] = {}

    def alias(self, keycloak_role: str, canonical: str) -> "PermissionRegistry":
        self._aliases[keycloak_role.strip().lower()] = canonical
        return self

    def grant(self, role: str, *permission_names: str) -> "PermissionRegistry":
        granted = blank_permission_grants()
        for permission_name in permission_names:
            granted[permission_name] = True
        self._grants[role] = granted
        return self

    def canonical_role(self, raw: str) -> str:
        raw = (raw or "").strip()
        return self._aliases.get(raw.lower(), raw.upper().replace("-", "_"))

    def permissions_for(self, role: str) -> dict[str, bool]:
        return self._grants.get(role, blank_permission_grants())

    def can(self, role: str, *permission_names: str) -> bool:
        granted = self.permissions_for(role)
        return any(granted.get(permission_name, False) for permission_name in permission_names)

    def known_roles(self) -> frozenset[str]:
        return frozenset(self._grants)


RBAC = (
    PermissionRegistry()
    .alias("platform-admin", "PLATFORM_ADMIN")
    .alias("ai-engineer", "AI_ENGINEER")
    .alias("executive-viewer", "EXECUTIVE")
    .grant("PLATFORM_ADMIN", *UNIVERSAL_PERMISSIONS, *RESTRICTED_PERMISSIONS)
    .grant("AI_ENGINEER", "toggleQdrant", "editExtractionPolicy", *UNIVERSAL_PERMISSIONS)
    .grant("EXECUTIVE", "editConflict", "processLayer", "addFilteringPolicy", *UNIVERSAL_PERMISSIONS)
    .grant("BUSINESS_OPERATOR", *UNIVERSAL_PERMISSIONS)
)


def authenticate(
        x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
        x_user_id: str = Header(default="", alias="X-User-Id"),
        x_user_name: str = Header(default="", alias="X-User-Name"),
        x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
        x_user_roles: str = Header(default="", alias="X-User-Roles"),
        x_role_id: str = Header(default="", alias="X-Role-Id"),
) -> dict[str, Any]:
    if x_kong_verified != "true":
        raise HTTPException(status_code=401, detail="Request not verified")

    raw_roles = [r for r in x_user_roles.split(",") if r.strip()]
    if not raw_roles and x_role_id:
        raw_roles = [x_role_id]
    roles = [RBAC.canonical_role(r) for r in raw_roles]

    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id or not _UUID_RE.match(tenant_id):
        raise HTTPException(status_code=400, detail="Kong-verified request missing valid tenant_id")

    role = roles[0] if roles else ""
    return {
        "role": role,
        "roles": roles,
        "role_id": resolve_role_id(role) if role else "",
        "tenant_id": tenant_id,
        "user_id": x_user_id or "",
        "user_name": (x_user_name or "").strip() or (x_user_id or ""),
    }

def require_perm(*permission_names: str) -> Callable[..., dict[str, Any]]:
    def _dep(ctx: dict[str, Any] = Depends(authenticate)) -> dict[str, Any]:
        if not RBAC.can(ctx["role"], *permission_names):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx['role'] or 'unknown'}' lacks permission: {' or '.join(permission_names)}",
            )
        return ctx

    return _dep
