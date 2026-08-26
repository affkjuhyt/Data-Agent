"""Shared FastAPI identity dependency for services that sit behind Kong.

`X-Kong-Verified: true` is set by the aeroflow-jwks plugin only after it has
verified a Keycloak JWT, and the plugin clears any client-supplied copy first
(infra/kong/plugins/aeroflow-jwks/handler.lua) — so through the gateway the
header is trustworthy. Reaching a service's port directly it is just a string,
and every service using this dependency would accept it. `X-Gateway-Key`
closes that: the plugin injects a shared secret alongside, which a caller that
never went through Kong cannot produce.

Same mechanism and same env var as services/kb_backend/dependencies/auth.py,
which additionally accepts signed service-to-service assertions
(libs/shared/service_identity.py) because it has a service caller;
the services using this helper are gateway-only.
"""
import hmac
import logging
import os
from typing import Any, Callable

from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)

GATEWAY_KEY_ENV = "KB_GATEWAY_KEY"


def _gateway_key_ok(presented: str) -> bool:
    """True when this request may be treated as gateway-verified.

    Unset secret preserves the previous behaviour so an existing deployment
    does not lock itself out on upgrade, but warns — in that state the only
    thing separating a caller from any identity is network reach.
    """
    expected = os.environ.get(GATEWAY_KEY_ENV, "")
    if not expected:
        logger.warning(
            "auth | %s is not set — trusting X-Kong-Verified without proof the request came "
            "from Kong. Any caller able to reach this service can impersonate any user.",
            GATEWAY_KEY_ENV,
        )
        return True
    # compare_digest: a plain == leaks the expected key through timing.
    return hmac.compare_digest(expected, presented or "")


def get_current_user(
    x_user_id: str = Header(..., alias="X-User-Id"),
    x_user_roles: str = Header(..., alias="X-User-Roles"),
    x_kong_verified: str = Header(default="", alias="X-Kong-Verified"),
    x_gateway_key: str = Header(default="", alias="X-Gateway-Key"),
    x_tenant_id: str = Header(default="", alias="X-Tenant-Id"),
) -> dict[str, Any]:
    if x_kong_verified != "true" or not _gateway_key_ok(x_gateway_key):
        raise HTTPException(status_code=401, detail="Request not verified by Kong gateway")
    return {
        "user_id":   x_user_id,
        "roles":     [r for r in x_user_roles.split(",") if r],
        "tenant_id": x_tenant_id,
    }


def require_role(*roles: str) -> Callable[..., dict[str, Any]]:
    def _dep(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if not any(r in user["roles"] for r in roles):
            raise HTTPException(status_code=403, detail=f"Required role: {' or '.join(roles)}")
        return user
    return _dep
