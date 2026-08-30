"""Shared caller authentication for services behind Kong.

One trust decision, used by every service, with exactly two ways in:

  1. **Kong**, proven by `X-Gateway-Key`. The aeroflow-jwks plugin injects the
     identity headers only after verifying a Keycloak JWT, and clears any
     client-supplied copies first — but `X-Kong-Verified: true` is still only a
     string, which any caller reaching the service port could send. The shared
     gateway key is what makes the marker mean "Kong verified this".
  2. **A signed service assertion** (backend/libs/shared/service_identity.py), for
     service-to-service calls that cannot carry a user's Keycloak token —
     orchestrator -> workflow-runtime, workflow-runtime -> kb-backend. Identity
     comes ENTIRELY from the verified token, so a service cannot sign a narrow
     assertion and widen it with headers.

Anything else is 401. `resolve_caller` is the single implementation of that
decision; `get_current_user` is the FastAPI dependency wrapping it, and
kb-backend layers its RBAC role canonicalisation on top of the same function
rather than repeating the logic.
"""
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from fastapi import Depends, Header, HTTPException

from libs.shared.service_identity import (
    ServiceAssertionError,
    get_secret,
    verify_service_assertion,
)

logger = logging.getLogger(__name__)

# Canonical name. KB_GATEWAY_KEY is accepted as a deprecated alias so a stack
# configured before this was shared across services keeps working.
GATEWAY_KEY_ENV = "GATEWAY_KEY"
GATEWAY_KEY_ENV_LEGACY = "KB_GATEWAY_KEY"


def gateway_key() -> str:
    return os.environ.get(GATEWAY_KEY_ENV, "") or os.environ.get(GATEWAY_KEY_ENV_LEGACY, "")


def allowed_services(env_var: str, default: str = "") -> frozenset[str]:
    """Services this process will accept an identity assertion from.

    Each entry can act as any user, so it is an explicit allowlist per service
    rather than "anyone holding the shared secret".
    """
    raw = os.environ.get(env_var, default)
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


@dataclass(frozen=True)
class ResolvedCaller:
    user_id: str
    roles: list[str]
    tenant_id: str
    # "gateway" | "service:<name>" — carried into logs and audit so a request
    # made on a user's behalf by a service is distinguishable from one the user
    # made directly.
    auth_source: str
    user_name: str = ""
    raw_role_id: str = ""
    assertion: dict[str, Any] = field(default_factory=dict)


def _gateway_key_ok(presented: str) -> bool:
    """True when this request may be treated as gateway-verified.

    An unset key preserves the previous behaviour so an existing deployment does
    not lock itself out mid-upgrade — but in that state the only thing between a
    caller and any identity is network reach, so it warns rather than passing
    silently.
    """
    expected = gateway_key()
    if not expected:
        logger.warning(
            "auth | %s is not set — trusting X-Kong-Verified without proof the request came "
            "from Kong. Any caller able to reach this service can impersonate any user.",
            GATEWAY_KEY_ENV,
        )
        return True
    # compare_digest, not ==: a plain comparison leaks the expected key by timing.
    return hmac.compare_digest(expected, presented or "")


def resolve_caller(
    *,
    x_kong_verified: str,
    x_gateway_key: str,
    x_service_assertion: str,
    x_user_id: str,
    x_user_roles: str,
    x_tenant_id: str,
    x_user_name: str = "",
    x_role_id: str = "",
    accepted_services: Iterable[str],
) -> ResolvedCaller:
    """Authenticate a request. Raises HTTPException(401) when neither route
    verifies — never returns a partially-trusted caller."""
    if x_service_assertion:
        try:
            payload = verify_service_assertion(
                x_service_assertion,
                secret=get_secret(),
                allowed_services=accepted_services,
            )
        except ServiceAssertionError as e:
            logger.warning("auth | rejected service assertion: %s", e)
            raise HTTPException(status_code=401, detail="Invalid service assertion") from e

        # Deliberately ignores every X-User-* header on this path.
        return ResolvedCaller(
            user_id=str(payload.get("uid", "")),
            user_name=str(payload.get("uid", "")),
            roles=[r for r in payload.get("roles", []) if r],
            tenant_id=str(payload.get("tid", "")),
            auth_source=f"service:{payload.get('svc', '')}",
            assertion=payload,
        )

    if x_kong_verified != "true" or not _gateway_key_ok(x_gateway_key):
        raise HTTPException(status_code=401, detail="Request not verified by Kong gateway")

    roles = [r.strip() for r in (x_user_roles or "").split(",") if r.strip()]
    return ResolvedCaller(
        user_id=x_user_id or "",
        user_name=(x_user_name or "").strip() or (x_user_id or ""),
        roles=roles,
        tenant_id=(x_tenant_id or "").strip(),
        auth_source="gateway",
        raw_role_id=x_role_id or "",
    )


def make_get_current_user(
    accepted_services_env: str, default_services: str = ""
) -> Callable[..., dict[str, Any]]:
    """Build a `get_current_user` dependency for one service.

    Each service names the peers it will accept assertions from via its own env
    var (orchestrator accepts none by default — nothing calls it service-to-
    service; workflow-runtime accepts the orchestrator).
    """

    def _dep(
        x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
        x_gateway_key: str = Header(default="", alias="X-Gateway-Key"),
        x_service_assertion: str = Header(default="", alias="X-Service-Assertion"),
        x_user_id: str = Header(default="", alias="X-User-Id"),
        x_user_roles: str = Header(default="", alias="X-User-Roles"),
        x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
        x_user_name: str = Header(default="", alias="X-User-Name"),
    ) -> dict[str, Any]:
        caller = resolve_caller(
            x_kong_verified=x_kong_verified,
            x_gateway_key=x_gateway_key,
            x_service_assertion=x_service_assertion,
            x_user_id=x_user_id,
            x_user_roles=x_user_roles,
            x_tenant_id=x_tenant_id,
            x_user_name=x_user_name,
            accepted_services=allowed_services(accepted_services_env, default_services),
        )
        return {
            "user_id":     caller.user_id,
            "user_name":   caller.user_name,
            "roles":       caller.roles,
            "tenant_id":   caller.tenant_id,
            "auth_source": caller.auth_source,
        }

    return _dep


# Default dependency for services with no service-to-service callers
# (auth-api, release-worker). X-User-Id / X-User-Roles were `Header(...)`
# (required) here before; they are optional now because the assertion path
# supplies them from the token instead, and a missing header must produce 401
# from the trust check rather than 422 from FastAPI validation.
get_current_user = make_get_current_user("ALLOWED_ASSERTING_SERVICES")


def require_tenant(caller: dict[str, Any]) -> str:
    """The caller's tenant, or 403. Never a default.

    Authentication proves WHO is calling; it does not by itself say which
    tenant's data they may touch. A verified caller can still arrive without a
    tenant — a Keycloak token missing the claim, a service assertion signed for
    a process rather than a tenant — and every call site that met that case
    reached for `or DEV_TENANT_ID`, which silently pointed the request at a real
    tenant's data. That is the same cross-tenant read the gateway was added to
    stop, just reached by a caller who did authenticate.

    Failing closed makes a misconfigured token a visible 403 instead of a quiet
    read of tenant 0000...0001.
    """
    tenant_id = (caller or {}).get("tenant_id") or ""
    if not tenant_id:
        logger.warning(
            "auth | caller user=%r source=%r has no tenant — refusing rather than "
            "falling back to a default tenant",
            (caller or {}).get("user_id"), (caller or {}).get("auth_source"),
        )
        raise HTTPException(status_code=403, detail="No tenant associated with this identity")
    return str(tenant_id)


def require_role(*roles: str) -> Callable[..., dict[str, Any]]:
    def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if not any(r in user["roles"] for r in roles):
            raise HTTPException(status_code=403, detail=f"Required role: {' or '.join(roles)}")
        return user
    return _dep
