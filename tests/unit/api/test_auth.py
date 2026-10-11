"""Token and capability checks (A1.2, ADR-30): the Quyền column of A2 guards each route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.kernel.auth import require

from .conftest import bearer

pytestmark = [pytest.mark.unit, pytest.mark.security]


def test_missing_token_is_401_problem(client: TestClient) -> None:
    r = client.get("/v1/conversations")
    assert r.status_code == 401
    assert r.headers["content-type"] == "application/problem+json"
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["code"] == "unauthenticated"


def test_invalid_token_is_401(client: TestClient) -> None:
    r = client.get("/v1/conversations", headers=bearer("forged"))
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_token_without_org_is_refused(client: TestClient) -> None:
    # RLS needs app.org_id; a token without one must not run unscoped.
    r = client.get("/v1/conversations", headers=bearer("no-org"))
    assert r.status_code == 403


def test_capability_from_a2_lets_the_caller_in(client: TestClient) -> None:
    r = client.get("/v1/conversations", headers=bearer("asker"))
    assert r.status_code == 200
    assert r.json() == {"user": "u-asker", "client": None, "caps": ["ask"]}


def test_missing_capability_is_403(client: TestClient) -> None:
    # GET /v1/crawl-sources needs admin_data or steward (A2, Thu thập).
    r = client.get("/v1/crawl-sources", headers=bearer("asker"))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_any_listed_capability_is_enough(client: TestClient) -> None:
    assert client.get("/v1/crawl-sources", headers=bearer("steward")).status_code == 200


def test_unknown_capabilities_never_reach_the_principal(client: TestClient) -> None:
    # The steward's resolver returns "made_up" too; only A1.2 capabilities survive.
    r = client.get("/v1/conversations", headers=bearer("steward"))
    assert r.status_code == 403


def test_acting_client_is_recorded(client: TestClient) -> None:
    r = client.get("/v1/conversations", headers=bearer("client"))
    assert r.json()["client"] == "mcp-client-1"


def test_require_rejects_a_capability_a2_does_not_have() -> None:
    with pytest.raises(ValueError):
        require("superuser")
