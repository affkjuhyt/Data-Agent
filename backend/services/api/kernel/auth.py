"""Who is calling: the Keycloak access token, verified here (A1.2, ADR-30).

ADR-30 keeps the check in the api even behind the gateway: the gateway routes
and rate-limits, it does not replace token and capability checks. So the token
is verified against the realm's JWKS (signature, issuer, expiry, audience)
on every request, and nothing is read from identity headers a caller could set.

The organisation comes from the `org_id` claim (A1.2: never a request field).
Who the caller is inside it, and what they may do, is not in the token: the
token's `sub` is the Keycloak id (`org.app_user.external_id`), and capabilities
live in `org.role.capabilities` and change without a re-login. The org module
resolves both through `IdentityResolver` once per request. With an
`X-Data-Space` header (A1.2, id or code) the caller's roles in that Data Space
join the organisation-wide ones.
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


DATA_SPACE_HEADER = "X-Data-Space"


@dataclass(frozen=True)
class Principal:
    # org.app_user.id: what app.user_id and every *_by column hold.
    user_id: uuid.UUID
    org_id: uuid.UUID
    # The token's `sub` (org.app_user.external_id).
    external_id: str
    # Set when a client acts for a user (token exchange, UC-12-01): audit records both.
    client_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    # The Data Space named by X-Data-Space, resolved to its id; None without the header.
    data_space_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Identity:
    user_id: uuid.UUID
    capabilities: frozenset[str]
    data_space_id: uuid.UUID | None = None


class DataSpaceNotFound(Exception):
    """The X-Data-Space header names no Data Space of the caller's organisation."""


class IdentityResolver(Protocol):
    async def resolve(
        self, *, org_id: uuid.UUID, external_id: str, data_space: str | None
    ) -> Identity | None:
        """The active app_user behind `external_id` and its capabilities, or None
        when the organisation has no such active user. Raises DataSpaceNotFound
        when `data_space` names no Data Space the user is a current member of."""
        ...


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
    resolver: IdentityResolver = request.app.state.identity_resolver
    try:
        identity = await resolver.resolve(
            org_id=org_id,
            external_id=str(claims["sub"]),
            data_space=request.headers.get(DATA_SPACE_HEADER) or None,
        )
    except DataSpaceNotFound as exc:
        raise Problem(404, "not_found", f"{DATA_SPACE_HEADER} names no Data Space.") from exc
    if identity is None:
        # Signed in at Keycloak but not (or no longer) an active user of this
        # organisation: directory sync has not run, or the account was disabled.
        raise Problem(403, "forbidden", "No active account in this organisation.")
    actor = claims.get("act")
    client_id = str(actor.get("sub")) if isinstance(actor, dict) and actor.get("sub") else None
    principal = Principal(
        user_id=identity.user_id,
        org_id=org_id,
        external_id=str(claims["sub"]),
        client_id=client_id,
        capabilities=identity.capabilities & CAPABILITIES,
        data_space_id=identity.data_space_id,
    )
    # Read back by the access log (kernel/access_log.py) once the response is known.
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def bearer_claims(request: Request) -> dict[str, Any] | None:
    """The verified claims of the request's bearer token, or None; read once per request.

    For the kernel's middlewares (rate limit, idempotency, access log), which run before the
    route and must not refuse a request themselves: a missing or invalid token is the route's
    own authentication to answer."""
    if hasattr(request.state, "bearer_claims"):
        cached: dict[str, Any] | None = request.state.bearer_claims
        return cached
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    claims: dict[str, Any] | None = None
    if scheme.lower() == "bearer" and token:
        try:
            claims = await run_in_threadpool(request.app.state.token_verifier.claims, token)
        except Exception:  # noqa: BLE001 - any failure means "no verified caller" here
            claims = None
    request.state.bearer_claims = claims
    return claims


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


async def current_data_space(principal: CurrentPrincipal) -> uuid.UUID:
    if principal.data_space_id is None:
        raise Problem(
            400, "data_space_required", f"This resource needs the {DATA_SPACE_HEADER} header."
        )
    return principal.data_space_id


# For resources that belong to a Data Space (A1.2): 400 data_space_required without the header.
CurrentDataSpace = Annotated[uuid.UUID, Depends(current_data_space)]
