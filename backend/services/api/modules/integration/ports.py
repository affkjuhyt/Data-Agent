"""What the Tích hợp group reaches outside PostgreSQL, as ports on app.state.

- `integration_credentials` (ClientCredentials): an api_client is a Keycloak confidential client
  with a service account (ADR-30): the adapter creates it with an `org_id` claim mapper, keeps
  its secret in OpenBao under tenants/<org>/… and returns that path as secret_ref. The secret is
  handed back once and never stored in PostgreSQL. Rotation keeps the old secret valid 24 hours
  (A3); revoking disables the Keycloak client, so no new token is issued.
- `integration_signing_keys` (SigningKeys): the HMAC key a webhook is signed with
  (X-DA-Signature, UC-12-03), kept in OpenBao and returned once.

Neither adapter exists in the api yet. Without one the routes answer 503 source_unavailable
instead of creating a client nobody can authenticate with; tests and the deploy wiring set them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from services.api.kernel.problems import Problem


@dataclass(frozen=True)
class IssuedCredentials:
    keycloak_client_id: str
    secret: str
    secret_ref: str


@dataclass(frozen=True)
class IssuedKey:
    key: str
    key_ref: str


class ClientCredentials(Protocol):
    async def create(self, *, org_id: uuid.UUID, name: str) -> IssuedCredentials: ...

    async def rotate(self, *, org_id: uuid.UUID, keycloak_client_id: str, secret_ref: str) -> str:
        """A new secret; the previous one keeps issuing tokens for 24 hours (A3)."""
        ...

    async def revoke(self, *, org_id: uuid.UUID, keycloak_client_id: str) -> None: ...


class SigningKeys(Protocol):
    async def create(self, *, org_id: uuid.UUID, subscription_id: uuid.UUID) -> IssuedKey: ...


def credentials(request: Request) -> ClientCredentials:
    port: ClientCredentials | None = getattr(request.app.state, "integration_credentials", None)
    if port is None:
        raise Problem(503, "source_unavailable", "The identity provider is not connected.")
    return port


def signing_keys(request: Request) -> SigningKeys:
    port: SigningKeys | None = getattr(request.app.state, "integration_signing_keys", None)
    if port is None:
        raise Problem(503, "source_unavailable", "The secret store is not connected.")
    return port
