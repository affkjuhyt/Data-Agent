import hmac
import logging
import os
import re
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException

from libs.shared.service_identity import (
    ServiceAssertionError,
    get_secret,
    verify_service_assertion,
)
from services.kb_backend.config import resolve_role_id

logger = logging.getLogger(__name__)

# Services allowed to assert an end user's identity to this backend. Adding a
# name here grants that service the power to act as any user, so it is a
# deliberate allowlist rather than "any holder of the secret".
_ALLOWED_ASSERTING_SERVICES = frozenset(
    s.strip() for s in os.environ.get("KB_ALLOWED_SERVICES", "workflow-runtime").split(",") if s.strip()
)

# Shared secret proving a request carrying X-Kong-Verified really came through
# Kong. The plugin sets it (infra/kong/plugins/aeroflow-jwks/handler.lua);
# nothing else knows it. Without this, X-Kong-Verified is a string anyone who
# can reach this port may send, which is precisely how the gateway's identity
# guarantees were being bypassed.
_GATEWAY_KEY_ENV = "KB_GATEWAY_KEY"


def _gateway_key_ok(presented: str) -> bool:
    """True when the Kong path may be trusted for this request.

    Unset KB_GATEWAY_KEY keeps the pre-existing behaviour so an existing
    deployment does not lock itself out on upgrade — but it is a downgrade, so
    it is logged loudly rather than passing silently. Set it in every
    environment that is not a laptop.
    """
    expected = os.environ.get(_GATEWAY_KEY_ENV, "")
    if not expected:
        logger.warning(
            "auth | %s is not set — trusting X-Kong-Verified without proof the request came from Kong. "
            "Any caller able to reach this service can impersonate any user.",
            _GATEWAY_KEY_ENV,
        )
        return True
    return hmac.compare_digest(expected, presented or "")

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


def _context_from(
    *, roles: list[str], tenant_id: str, user_id: str, user_name: str, source: str,
) -> dict[str, Any]:
    canonical = [RBAC.canonical_role(r) for r in roles]
    tenant_id = (tenant_id or "").strip()
    if not tenant_id or not _UUID_RE.match(tenant_id):
        raise HTTPException(status_code=400, detail=f"{source} request missing valid tenant_id")

    # First role wins, and role_id derives from it — so the caller-supplied
    # ORDER of roles decides effective privilege. Harmless when the list is
    # authentic (Kong emits the token's roles, the assertion is signed), but it
    # is why neither path may take roles from an unauthenticated header.
    role = canonical[0] if canonical else ""
    return {
        "role": role,
        "roles": canonical,
        "role_id": resolve_role_id(role) if role else "",
        "tenant_id": tenant_id,
        "user_id": user_id or "",
        "user_name": (user_name or "").strip() or (user_id or ""),
    }


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
    """Resolve the caller, by exactly one of two authenticated routes.

    1. A signed service assertion (workflow-runtime and anything else in
       KB_ALLOWED_SERVICES). Identity comes ENTIRELY from the verified token —
       the X-User-* headers are ignored on this path, so a service cannot sign
       a narrow assertion and widen it with headers.
    2. Kong, proven by X-Gateway-Key. Identity comes from the headers Kong
       injects after verifying the Keycloak JWT, having first cleared any
       client-supplied copies.

    A request with neither is rejected. This is the fix for agent traffic
    arriving with a self-declared `X-Kong-Verified: true` and a hardcoded admin
    role set; there is no longer a route that accepts unproven identity.
    """
    if x_service_assertion:
        try:
            payload = verify_service_assertion(
                x_service_assertion,
                secret=get_secret(),
                allowed_services=_ALLOWED_ASSERTING_SERVICES,
            )
        except ServiceAssertionError as e:
            logger.warning("auth | rejected service assertion: %s", e)
            raise HTTPException(status_code=401, detail="Invalid service assertion") from e

        ctx = _context_from(
            roles=list(payload.get("roles", [])),
            tenant_id=str(payload.get("tid", "")),
            user_id=str(payload.get("uid", "")),
            user_name=str(payload.get("uid", "")),
            source="Service-asserted",
        )
        ctx["auth_source"] = f"service:{payload.get('svc', '')}"
        return ctx

    if x_kong_verified != "true":
        raise HTTPException(status_code=401, detail="Request not verified")

    if not _gateway_key_ok(x_gateway_key):
        logger.warning("auth | X-Kong-Verified presented without a valid gateway key — rejecting")
        raise HTTPException(status_code=401, detail="Request not verified")

    raw_roles = [r for r in x_user_roles.split(",") if r.strip()]
    if not raw_roles and x_role_id:
        raw_roles = [x_role_id]

    ctx = _context_from(
        roles=raw_roles,
        tenant_id=x_tenant_id,
        user_id=x_user_id,
        user_name=x_user_name,
        source="Kong-verified",
    )
    ctx["auth_source"] = "gateway"
    return ctx

def require_perm(*permission_names: str) -> Callable[..., dict[str, Any]]:
    def _dep(ctx: dict[str, Any] = Depends(authenticate)) -> dict[str, Any]:
        if not RBAC.can(ctx["role"], *permission_names):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx['role'] or 'unknown'}' lacks permission: {' or '.join(permission_names)}",
            )
        return ctx

    return _dep
