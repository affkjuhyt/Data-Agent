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
from services.api.kernel.auth import CurrentPrincipal

ORG = uuid.UUID("01920000-0000-7000-8000-000000000001")


class FakeVerifier:
    """Tokens are the claims as JSON-ish keys of TOKENS; anything else is invalid."""

    def __init__(self, tokens: dict[str, dict[str, Any]]) -> None:
        self.tokens = tokens

    def claims(self, token: str) -> dict[str, Any]:
        if token not in self.tokens:
            raise jwt.InvalidTokenError("unknown token")
        return self.tokens[token]


class FakeCapabilities:
    def __init__(self, by_user: dict[str, frozenset[str]]) -> None:
        self.by_user = by_user

    async def capabilities(self, *, org_id: uuid.UUID, user_id: str) -> frozenset[str]:
        return self.by_user.get(user_id, frozenset())


TOKENS = {
    "asker": {"sub": "u-asker", "org_id": str(ORG)},
    "steward": {"sub": "u-steward", "org_id": str(ORG)},
    "no-org": {"sub": "u-x"},
    "client": {"sub": "u-asker", "org_id": str(ORG), "act": {"sub": "mcp-client-1"}},
}
CAPS = {
    "u-asker": frozenset({"ask"}),
    # "made_up" is not a capability of A1.2 and must never reach a Principal.
    "u-steward": frozenset({"steward", "made_up"}),
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
            "user": principal.user_id,
            "client": principal.client_id,
            "caps": sorted(principal.capabilities),
        }

    @router.get(
        "/v1/crawl-sources", **endpoint("GET", "/v1/crawl-sources", operation_id="listCrawlSources")
    )
    async def crawl_sources() -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/v1/answers/{id}", **endpoint("GET", "/v1/answers/{id}", operation_id="getAnswer"))
    async def boom(id: str) -> None:
        raise RuntimeError("boom")

    return router


@pytest.fixture
def app() -> FastAPI:
    app = create_app(
        Settings(),
        token_verifier=FakeVerifier(TOKENS),
        capability_resolver=FakeCapabilities(CAPS),
    )
    app.include_router(probe_router())
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
