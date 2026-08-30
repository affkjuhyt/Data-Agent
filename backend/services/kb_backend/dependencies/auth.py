import logging
import re
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException

from libs.shared.auth import allowed_services, resolve_caller
from services.kb_backend.config import resolve_role_id

logger = logging.getLogger(__name__)

# Services allowed to assert an end user's identity to this backend. Adding a
# name here grants that service the power to act as any user, so it is a
# deliberate allowlist rather than "any holder of the secret".
_ALLOWED_SERVICES_ENV = "KB_ALLOWED_SERVICES"
_DEFAULT_ALLOWED_SERVICES = "workflow_runtime"

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
        x_gateway_key: str = Header(default="", alias="X-Gateway-Key"),
        x_service_assertion: str = Header(default="", alias="X-Service-Assertion"),
        x_user_id: str = Header(default="", alias="X-User-Id"),
        x_user_name: str = Header(default="", alias="X-User-Name"),
        x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
        x_user_roles: str = Header(default="", alias="X-User-Roles"),
        x_role_id: str = Header(default="", alias="X-Role-Id"),
) -> dict[str, Any]:
    """Resolve the caller, then map their roles onto this service's RBAC.

    The trust decision — signed service assertion, or Kong proven by the shared
    gateway key, and nothing else — lives in backend/libs/shared/auth.resolve_caller so
    every service makes it identically. What stays here is kb-backend's own
    layer: canonicalising Keycloak role names and deriving the role_id that
    operations/access.py filters documents by.
    """
    caller = resolve_caller(
        x_kong_verified=x_kong_verified,
        x_gateway_key=x_gateway_key,
        x_service_assertion=x_service_assertion,
        x_user_id=x_user_id,
        x_user_roles=x_user_roles,
        x_tenant_id=x_tenant_id,
        x_user_name=x_user_name,
        x_role_id=x_role_id,
        accepted_services=allowed_services(_ALLOWED_SERVICES_ENV, _DEFAULT_ALLOWED_SERVICES),
    )

    raw_roles = list(caller.roles)
    # Kong sends X-Role-Id when the token carries no role claim; the assertion
    # path has no equivalent, its roles list is authoritative.
    if not raw_roles and caller.raw_role_id:
        raw_roles = [caller.raw_role_id]
    roles = [RBAC.canonical_role(r) for r in raw_roles]

    if not caller.tenant_id or not _UUID_RE.match(caller.tenant_id):
        raise HTTPException(status_code=400, detail="Verified request missing valid tenant_id")

    # First role wins, and role_id derives from it — so the ORDER of roles
    # decides effective privilege. Safe only because both routes above deliver
    # an authenticated list: Kong emits the token's roles, the assertion is
    # signed. It is why neither may take roles from a bare header.
    role = roles[0] if roles else ""
    return {
        "role": role,
        "roles": roles,
        "role_id": resolve_role_id(role) if role else "",
        "tenant_id": caller.tenant_id,
        "user_id": caller.user_id,
        "user_name": caller.user_name,
        "auth_source": caller.auth_source,
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
