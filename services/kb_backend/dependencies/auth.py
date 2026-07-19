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
from typing import Any, Callable, Iterable

from fastapi import Depends, Header, HTTPException

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


class PermissionRegistry:
    """Single source of truth for RBAC: the Keycloak realm-role → canonical-role
    aliasing and the canonical-role → permission-set grants.

    Replaces the bare module-level dicts with an explicit, testable object.
    Registration is fluent (chainable) so the policy reads as a declarative
    block; lookups are methods so callers never poke at the raw maps:

        RBAC.canonical_role("platform-admin")   -> "PLATFORM_ADMIN"
        RBAC.can("AI_ENGINEER", "toggle_qdrant") -> True
        RBAC.permissions_for("EXECUTIVE")        -> frozenset({...})

    Grants are stored as frozensets so a caller can't mutate the policy by
    holding a reference to a returned set.
    """

    __slots__ = ("_grants", "_aliases")

    def __init__(self) -> None:
        self._grants: dict[str, frozenset[str]] = {}
        self._aliases: dict[str, str] = {}

    # ── registration (fluent) ─────────────────────────────────────────────────
    def alias(self, keycloak_role: str, canonical: str) -> "PermissionRegistry":
        """Map a Keycloak realm role name to a canonical app role."""
        self._aliases[keycloak_role.strip().lower()] = canonical
        return self

    def grant(self, role: str, perms: Iterable[str]) -> "PermissionRegistry":
        """Declare the full permission set a canonical role holds."""
        self._grants[role] = frozenset(perms)
        return self

    # ── lookups ───────────────────────────────────────────────────────────────
    def canonical_role(self, raw: str) -> str:
        """Resolve a raw Keycloak role to its canonical form. Unknown names fall
        back to UPPER_SNAKE_CASE so an unmapped realm role is still deterministic
        (and simply grants nothing until explicitly registered)."""
        raw = (raw or "").strip()
        return self._aliases.get(raw.lower(), raw.upper().replace("-", "_"))

    def permissions_for(self, role: str) -> frozenset[str]:
        """The permission set granted to a canonical role (empty if unknown)."""
        return self._grants.get(role, frozenset())

    def can(self, role: str, *perms: str) -> bool:
        """True if `role` grants ANY of `perms` (the require_perm semantics)."""
        granted = self._grants.get(role, frozenset())
        return any(p in granted for p in perms)

    def known_roles(self) -> frozenset[str]:
        return frozenset(self._grants)

    def all_permissions(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        return out.union(*self._grants.values()) if self._grants else out


# ── Policy definition ──────────────────────────────────────────────────────────
# Keep in sync with src/lib/permissions.ts → ROLE_PERMISSIONS. The aliases match
# config.ROLE_PERMISSION_IDS keys.
_ALL = {
    "delete_data", "edit_conflict", "process_layer", "add_data", "add_warehouse",
    "add_filtering_policy", "edit_filtering_policy", "delete_filtering_policy",
    "edit_extraction_policy", "toggle_qdrant", "add_warehouse_config",
    "edit_warehouse_config", "add_chunk_version",
}

RBAC = (
    PermissionRegistry()
    .alias("platform-admin",   "PLATFORM_ADMIN")
    .alias("ai-engineer",      "AI_ENGINEER")
    .alias("executive-viewer", "EXECUTIVE")
    .grant("PLATFORM_ADMIN", _ALL)
    .grant("AI_ENGINEER",    {"toggle_qdrant", "edit_extraction_policy"})
    .grant("EXECUTIVE",      {"edit_conflict", "process_layer", "add_filtering_policy"})
)


def authenticate(
    x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
    x_user_id:       str = Header(default="", alias="X-User-Id"),
    x_user_name:     str = Header(default="", alias="X-User-Name"),
    x_tenant_id:     str = Header(default="", alias="X-Tenant-Id"),
    x_user_roles:    str = Header(default="", alias="X-User-Roles"),
    x_role_id:       str = Header(default="", alias="X-Role-Id"),
) -> dict[str, Any]:
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
    roles = [RBAC.canonical_role(r) for r in raw_roles]

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


def require_perm(*perms: str) -> Callable[..., dict[str, Any]]:
    """Dependency factory: 403 unless the caller's role grants any of `perms`."""
    def _dep(ctx: dict[str, Any] = Depends(authenticate)) -> dict[str, Any]:
        if not RBAC.can(ctx["role"], *perms):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx['role'] or 'unknown'}' lacks permission: {' or '.join(perms)}",
            )
        return ctx
    return _dep
