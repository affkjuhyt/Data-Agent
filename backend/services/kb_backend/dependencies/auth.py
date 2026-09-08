import logging
import re
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException, Request

from libs.database_connector.postgres_connector import set_current_tenant
from libs.shared.auth import allowed_services, resolve_caller
from services.kb_backend.config import resolve_role_id
from services.kb_backend.dependencies.permissions import (
    PERMISSIONS,
    PermissionStoreUnavailable,
    UnknownPermission,
    store,
)

logger = logging.getLogger(__name__)

# Services allowed to assert an end user's identity to this backend. Adding a
# name here grants that service the power to act as any user, so it is a
# deliberate allowlist rather than "any holder of the secret".
_ALLOWED_SERVICES_ENV = "KB_ALLOWED_SERVICES"
_DEFAULT_ALLOWED_SERVICES = "workflow_runtime"

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)

# Keycloak realm-role name → this service's canonical spelling. Only the
# irregular ones need an entry; anything else is upper-cased with dashes turned
# into underscores, so `business-operator` → `BUSINESS_OPERATOR` for free.
#
# This is NOT the permission matrix — that lives in the RolePermissions table
# (see dependencies/permissions.py). It is the name normalisation that has to
# happen first, so that the role in a JWT and the role_id in a database row are
# the same string.
_ROLE_ALIASES: dict[str, str] = {
    "platform-admin":   "PLATFORM_ADMIN",
    "ai-engineer":      "AI_ENGINEER",
    "executive-viewer": "EXECUTIVE",
}


def canonical_role(raw: str) -> str:
    raw = (raw or "").strip()
    return _ROLE_ALIASES.get(raw.lower(), raw.upper().replace("-", "_"))


async def authenticate(
        x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
        x_gateway_key: str = Header(default="", alias="X-Gateway-Key"),
        x_service_assertion: str = Header(default="", alias="X-Service-Assertion"),
        x_user_id: str = Header(default="", alias="X-User-Id"),
        x_user_name: str = Header(default="", alias="X-User-Name"),
        x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
        x_user_roles: str = Header(default="", alias="X-User-Roles"),
        x_role_id: str = Header(default="", alias="X-Role-Id"),
) -> dict[str, Any]:
    """Resolve the caller and canonicalise their roles.

    The trust decision — signed service assertion, or Kong proven by the shared
    gateway key, and nothing else — lives in backend/libs/shared/auth.resolve_caller so
    every service makes it identically. What stays here is kb-backend's own
    layer: canonicalising Keycloak role names and deriving the role_id that
    operations/access.py filters documents by. What the role may DO is a
    separate question, answered from Postgres by require_perm.
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
    roles = [canonical_role(r) for r in raw_roles]

    if not caller.tenant_id or not _UUID_RE.match(caller.tenant_id):
        raise HTTPException(status_code=400, detail="Verified request missing valid tenant_id")

    # First role wins, and role_id derives from it — so the ORDER of roles
    # decides effective privilege. Safe only because both routes above deliver
    # an authenticated list: Kong emits the token's roles, the assertion is
    # signed. It is why neither may take roles from a bare header.
    role = roles[0] if roles else ""
    # Every session opened for this request scopes itself to this tenant.
    # async so the ContextVar lands in the request's task, not a threadpool.
    set_current_tenant(caller.tenant_id)
    return {
        "role": role,
        "roles": roles,
        "role_id": resolve_role_id(role) if role else "",
        "tenant_id": caller.tenant_id,
        "user_id": caller.user_id,
        "user_name": caller.user_name,
        "auth_source": caller.auth_source,
    }


def postgres_for(request: Request) -> Any:
    """The Postgres client the permission matrix is read from.

    Reached through app.state rather than imported from main, both to avoid the
    import cycle and so a test can supply its own.
    """
    postgres = getattr(request.app.state, "postgres", None)
    if postgres is None:
        raise HTTPException(
            status_code=503,
            detail="Permission store unavailable: this deployment has no Postgres client.",
        )
    return postgres


def require_perm(*permission_names: str) -> Callable[..., Any]:
    """Authorise the caller for ANY of `permission_names`, per RolePermissions.

    A role that holds none of them is a 403. A matrix that cannot be read at
    all is a 503 — the request is not denied on policy, it is unanswerable, and
    reporting that as 403 would send an operator hunting for a missing grant
    that exists.

    Names are checked against the catalogue HERE, as the route is being
    declared, so a typo is an ImportError at startup rather than a route that
    every role is quietly refused.
    """
    unknown = sorted(set(permission_names) - PERMISSIONS)
    if unknown:
        raise UnknownPermission(
            f"require_perm({', '.join(map(repr, permission_names))}) names "
            f"{unknown}, which the catalogue in dependencies/permissions.py does not "
            "define. Add it there (and seed a RolePermissions row) or fix the spelling."
        )

    async def _dep(
            request: Request,
            ctx: dict[str, Any] = Depends(authenticate),
    ) -> dict[str, Any]:
        try:
            permitted = await store.can(postgres_for(request), ctx["role"], *permission_names)
        except PermissionStoreUnavailable:
            raise HTTPException(
                status_code=503,
                detail="Permission store unavailable — cannot authorise this request.",
            ) from None
        if not permitted:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx['role'] or 'unknown'}' lacks permission: {' or '.join(permission_names)}",
            )
        return ctx

    return _dep
