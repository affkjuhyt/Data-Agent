"""Who is calling: the Keycloak access token, verified here (A1.2, ADR-30).

ADR-30 keeps the check in the api even behind the gateway: the gateway routes
and rate-limits, it does not replace token and capability checks. So the token
is verified against the realm's JWKS (signature, issuer, expiry, audience)
on every request, and nothing is read from identity headers a caller could set.

The organisation comes from the `org_id` claim (A1.2: never a request field).
Capabilities are not in the token: they live in `org.role.capabilities` and
change without a re-login, so the org module resolves them through
`CapabilityResolver` once per request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from services.api.kernel.problems import Problem

# Capabilities of A1.2 plus exec_vault (tab 4, Lớp riêng lãnh đạo). The three
# special values of the "Quyền" column (channel, client, —) are not
# capabilities: they describe how the caller authenticates.
CAPABILITIES = frozenset(
    {
        "ask",
        "view_detail",
        "upload",
        "prepare_data",
        "admin_data",
        "steward",
        "approve",
        "create_skill",
        "run_agent",
        "create_agent",
        "writeback",
        "pack_author",
        "admin",
        "compliance",
        "audit_read",
        "exec_vault",
    }
)


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: uuid.UUID
    # Set when a client acts for a user (token exchange, UC-12-01): audit records both.
    client_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)


class CapabilityResolver(Protocol):
    async def capabilities(self, *, org_id: uuid.UUID, user_id: str) -> frozenset[str]: ...


class TokenVerifier(Protocol):
    def claims(self, token: str) -> dict[str, Any]: ...


class JwksTokenVerifier:
    """Verifies RS256/ES256 tokens against the realm's published keys.

    PyJWKClient caches the key set and refetches on an unknown `kid`, so a
    Keycloak key rotation needs no restart.
    """

    def __init__(self, *, issuer: str, audience: str, jwks_url: str | None = None) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwt.PyJWKClient(
            jwks_url or f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
        )

    def claims(self, token: str) -> dict[str, Any]:
        key = self._jwks.get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "ES256"],
            issuer=self._issuer,
            audience=self._audience,
            options={"require": ["exp", "iss", "sub"]},
        )
        return claims


_bearer = HTTPBearer(auto_error=False)


def _unauthenticated(detail: str) -> Problem:
    return Problem(401, "unauthenticated", detail, headers={"WWW-Authenticate": "Bearer"})


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated("Missing bearer token.")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        # In the threadpool: a JWKS refetch is a blocking HTTP call.
        claims = await run_in_threadpool(verifier.claims, credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _unauthenticated("Invalid or expired token.") from exc
    try:
        org_id = uuid.UUID(str(claims["org_id"]))
    except (KeyError, ValueError) as exc:
        # A token without an organisation cannot be scoped by RLS; refuse it
        # rather than run with app.org_id unset.
        raise Problem(403, "forbidden", "The token carries no organisation.") from exc
    user_id = str(claims["sub"])
    resolver: CapabilityResolver = request.app.state.capability_resolver
    capabilities = await resolver.capabilities(org_id=org_id, user_id=user_id)
    actor = claims.get("act")
    client_id = str(actor.get("sub")) if isinstance(actor, dict) and actor.get("sub") else None
    return Principal(
        user_id=user_id,
        org_id=org_id,
        client_id=client_id,
        capabilities=capabilities & CAPABILITIES,
    )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(*any_of: str) -> Any:
    """Dependency: the caller holds at least one of `any_of` (A2's "Quyền" lists
    alternatives, e.g. "admin_data, steward"). No argument means any signed-in user."""
    unknown = set(any_of) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities: {sorted(unknown)}")

    async def check(principal: CurrentPrincipal) -> Principal:
        if any_of and not principal.capabilities.intersection(any_of):
            raise Problem(403, "forbidden", "Missing capability: " + " or ".join(any_of) + ".")
        return principal

    return Depends(check)
