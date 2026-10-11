"""An api app with a fake token verifier and capability table, so the kernel is
tested without Keycloak or Postgres."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.catalogue import endpoint
from services.api.config import Settings
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, DataSpaceNotFound, Identity

ORG = uuid.UUID("01920000-0000-7000-8000-000000000001")


class FakeVerifier:
    """Tokens are the claims as JSON-ish keys of TOKENS; anything else is invalid."""

    def __init__(self, tokens: dict[str, dict[str, Any]]) -> None:
        self.tokens = tokens

    def claims(self, token: str) -> dict[str, Any]:
        if token not in self.tokens:
            raise jwt.InvalidTokenError("unknown token")
        return self.tokens[token]


SPACE = uuid.UUID("01920000-0000-7000-8000-0000000000d5")


class FakeIdentities:
    """Users by token `sub`; Data Space "DS-OIL" (or its id) adds `space_caps`."""

    def __init__(self, users: dict[str, tuple[uuid.UUID, frozenset[str], frozenset[str]]]) -> None:
        self.users = users

    async def resolve(
        self, *, org_id: uuid.UUID, external_id: str, data_space: str | None
    ) -> Identity | None:
        if external_id not in self.users:
            return None
        user_id, caps, space_caps = self.users[external_id]
        if data_space is None:
            return Identity(user_id=user_id, capabilities=caps)
        if data_space not in ("DS-OIL", str(SPACE)):
            raise DataSpaceNotFound(data_space)
        return Identity(user_id=user_id, capabilities=caps | space_caps, data_space_id=SPACE)


TOKENS = {
    "asker": {"sub": "kc-asker", "org_id": str(ORG)},
    "steward": {"sub": "kc-steward", "org_id": str(ORG)},
    "no-org": {"sub": "kc-asker"},
    "stranger": {"sub": "kc-unknown", "org_id": str(ORG)},
    "client": {"sub": "kc-asker", "org_id": str(ORG), "act": {"sub": "mcp-client-1"}},
}
ASKER = uuid.UUID("01920000-0000-7000-8000-00000000a5c1")
STEWARD = uuid.UUID("01920000-0000-7000-8000-00000000057e")
USERS = {
    "kc-asker": (ASKER, frozenset({"ask"}), frozenset({"admin_data"})),
    # "made_up" is not a capability of A1.2 and must never reach a Principal.
    "kc-steward": (STEWARD, frozenset({"steward", "made_up"}), frozenset()),
}


def probe_router() -> APIRouter:
    """Two real A2 endpoints wired to echo the principal: one `ask`, one
    `admin_data, steward`, plus a crash for the 500 path."""
    router = APIRouter()

    @router.get(
        "/v1/conversations",
        **endpoint("GET", "/v1/conversations", operation_id="listConversations"),
    )
    async def conversations(principal: CurrentPrincipal) -> dict[str, Any]:
        return {
            "user": str(principal.user_id),
            "client": principal.client_id,
            "caps": sorted(principal.capabilities),
            "space": str(principal.data_space_id) if principal.data_space_id else None,
        }

    @router.get(
        "/v1/crawl-sources", **endpoint("GET", "/v1/crawl-sources", operation_id="listCrawlSources")
    )
    async def crawl_sources(space: CurrentDataSpace) -> dict[str, str]:
        return {"space": str(space)}

    @router.get("/v1/answers/{id}", **endpoint("GET", "/v1/answers/{id}", operation_id="getAnswer"))
    async def boom(id: str) -> None:
        raise RuntimeError("boom")

    return router


@pytest.fixture
def app() -> FastAPI:
    app = create_app(
        Settings(),
        token_verifier=FakeVerifier(TOKENS),
        identity_resolver=FakeIdentities(USERS),
    )
    app.include_router(probe_router())
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
