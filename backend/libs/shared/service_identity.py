"""Signed service-to-service identity assertions.

Solves a specific hole: kb-backend's `authenticate` trusts `X-Kong-Verified:
true` plus whatever `X-User-Roles` accompanies it. Kong sets those headers only
after verifying a Keycloak JWT (and clears any client-supplied copies first —
deploy/docker/kong/plugins/aeroflow-jwks/handler.lua), so through the gateway they are
trustworthy. Reaching kb-backend's port directly, they are just strings: any
caller on the network could claim to be a platform admin. workflow_runtime's
kb_search node did exactly that, hardcoding an admin role set on every agent
retrieval, which made kb-backend's document-level RBAC unenforceable for agent
traffic.

An assertion is a compact HMAC-SHA256 token binding *which service* is calling
and *whose identity* it is acting for, with an expiry so a captured token is
useless within minutes:

    v1.<base64url(payload json)>.<base64url(hmac_sha256(secret, signing_input))>

Deliberately not a full JWT library: the payload is fixed and tiny, both ends
live in this repo, and there is no third-party issuer, so a dependency-free
HMAC keeps `backend/libs/shared` a leaf package (enforced by .importlinter) and leaves
no algorithm-confusion surface — there is one algorithm and `alg` is not a
field an attacker can influence.

This is the per-request proof of *who is asserting what*. It composes with, and
does not replace, transport-level mutual TLS (deploy/docker/certs/gen-certs.sh), which
proves the peer holds a CA-signed certificate. Either alone is a real
improvement; together the caller is authenticated at both layers.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Iterable

_ALG_PREFIX = "v1"
# Short by design: an assertion is minted per outbound call and consumed
# immediately, so minutes of validity are generous. Long enough to absorb clock
# skew between containers and a slow retry, short enough that a token lifted
# from a log is worthless by the time anyone reads it.
DEFAULT_TTL_S = 120
# Tolerance for clock skew when checking `exp`/`iat` across hosts.
_CLOCK_SKEW_S = 30

SERVICE_AUTH_SECRET_ENV = "SERVICE_AUTH_SECRET"


class ServiceAssertionError(Exception):
    """Raised whenever an assertion is absent, malformed, expired, wrongly
    signed, or from an unexpected service. Callers must treat every instance as
    "unauthenticated" — the distinctions exist for logs, never to let a request
    through."""


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def get_secret() -> str:
    """Shared HMAC secret, from the environment.

    Both minting and verifying services must be given the same value (in
    production out of OpenBao, like every other credential here — see
    backend/services/workflow_runtime/warehouse_secrets.py). Returns "" when unset;
    callers decide what that means, because the two sides want opposite
    defaults: a minter with no secret should skip the header and let the
    request be rejected downstream, while a verifier with no secret must not
    silently accept everything.
    """
    return os.environ.get(SERVICE_AUTH_SECRET_ENV, "")


def mint_service_assertion(
    *,
    service: str,
    tenant_id: str,
    user_id: str,
    roles: Iterable[str],
    secret: str,
    ttl_s: int = DEFAULT_TTL_S,
) -> str:
    """Sign an assertion that `service` is calling on behalf of `user_id`.

    `roles` must be the roles the real end user actually holds. Passing a
    broader set here is the same privilege escalation as forging the header —
    the signature makes the claim authentic, not correct.
    """
    if not secret:
        raise ServiceAssertionError("cannot mint a service assertion without a secret")

    now = int(time.time())
    payload = {
        "svc":   service,
        "tid":   tenant_id,
        "uid":   user_id,
        "roles": [r for r in roles if r],
        "iat":   now,
        "exp":   now + int(ttl_s),
    }
    payload_b64 = _b64u_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{_ALG_PREFIX}.{payload_b64}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64u_encode(signature)}"


def verify_service_assertion(
    token: str,
    *,
    secret: str,
    allowed_services: Iterable[str],
) -> dict[str, Any]:
    """Verify and decode an assertion, or raise ServiceAssertionError.

    Order matters: the signature is checked BEFORE the payload is interpreted,
    so nothing an unauthenticated caller wrote is ever acted on. `exp` and the
    service allowlist are checked after, on payload that is known authentic.
    """
    if not secret:
        raise ServiceAssertionError("service assertion verification is not configured (no secret)")
    if not token:
        raise ServiceAssertionError("missing service assertion")

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _ALG_PREFIX:
        raise ServiceAssertionError("malformed service assertion")

    _, payload_b64, signature_b64 = parts
    signing_input = f"{_ALG_PREFIX}.{payload_b64}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()

    try:
        provided = _b64u_decode(signature_b64)
    except Exception as e:
        raise ServiceAssertionError("malformed service assertion signature") from e

    # compare_digest, not ==, so a wrong signature costs the same time whatever
    # its first differing byte — otherwise the comparison leaks the expected
    # signature one byte at a time.
    if not hmac.compare_digest(expected, provided):
        raise ServiceAssertionError("service assertion signature mismatch")

    try:
        payload: dict[str, Any] = json.loads(_b64u_decode(payload_b64))
    except Exception as e:
        raise ServiceAssertionError("malformed service assertion payload") from e

    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int) or now > exp + _CLOCK_SKEW_S:
        raise ServiceAssertionError("service assertion expired")
    iat = payload.get("iat")
    if not isinstance(iat, int) or iat > now + _CLOCK_SKEW_S:
        raise ServiceAssertionError("service assertion issued in the future")

    allowed = {s for s in allowed_services if s}
    service = payload.get("svc", "")
    if service not in allowed:
        raise ServiceAssertionError(f"service {service!r} is not permitted to assert identity")

    roles = payload.get("roles")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise ServiceAssertionError("service assertion has a malformed roles claim")

    return payload
