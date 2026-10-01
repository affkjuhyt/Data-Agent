"""HTTP-level tests for backend/services/auth_api/routers/compliance.py — the
checks that must refuse a request before it reaches the database, and the
DB-free PII scan.

`pii_masking` stored here is read by the KB worker at ingestion to decide
whether PII is redacted, so only platform-admin may reach the settings write.
The router runs behind the real `require_role` dependency; `get_session` is a
tripwire that fails the request if it is opened. Tenant scoping, normalisation of
what is stored and the score over stored settings are in
tests/integration/auth_api/test_compliance_router.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth_api.routers import compliance

pytestmark = pytest.mark.unit

_GATEWAY_KEY = "gw-test-key"
_TENANT = "tenant-a"


def _headers(roles: str = "platform-admin", **overrides: str) -> dict[str, str]:
    headers = {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": _GATEWAY_KEY,
        "X-User-Id": "user-1",
        "X-User-Roles": roles,
        "X-Tenant-Id": _TENANT,
    }
    headers.update(overrides)
    return headers


@pytest.fixture(autouse=True)
def _gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A set key makes the real trust check run instead of the unset-key fallback.
    monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every attempt to open a DB session; each one also fails the request."""
    calls: list[str] = []

    def _tripwire() -> Any:
        calls.append("get_session")
        raise AssertionError("the request reached the database")

    monkeypatch.setattr(compliance, "get_session", _tripwire)
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(compliance.router)
    return TestClient(app, raise_server_exceptions=False)


def test_routes_are_registered() -> None:
    registered = {
        (method, route.path)
        for route in compliance.router.routes
        for method in getattr(route, "methods", ())
    }
    assert registered >= {
        ("GET", "/api/auth/compliance/settings"),
        ("PUT", "/api/auth/compliance/settings"),
        ("GET", "/api/auth/compliance/score"),
        ("POST", "/api/auth/compliance/pii-scan"),
    }


class TestAuthGuard:
    def test_request_not_marked_by_kong_is_401(self, client: TestClient, opened: list[str]) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/compliance/settings", headers=headers).status_code == 401
        assert opened == []

    def test_wrong_gateway_key_is_401(self, client: TestClient, opened: list[str]) -> None:
        headers = _headers(**{"X-Gateway-Key": "forged"})
        assert client.get("/api/auth/compliance/settings", headers=headers).status_code == 401
        assert opened == []

    def test_tenant_admin_cannot_switch_pii_masking(
        self, client: TestClient, opened: list[str]
    ) -> None:
        resp = client.put(
            "/api/auth/compliance/settings",
            json={"pii_masking": False},
            headers=_headers(roles="tenant-admin"),
        )

        assert resp.status_code == 403
        assert opened == []


class TestSettings:
    def test_put_with_wrong_body_type_is_422(self, client: TestClient, opened: list[str]) -> None:
        resp = client.put(
            "/api/auth/compliance/settings",
            json={"retention_days": "forever"},
            headers=_headers(),
        )

        assert resp.status_code == 422
        assert opened == []


class TestPiiScan:
    def test_masks_without_echoing_the_raw_value(
        self, client: TestClient, opened: list[str]
    ) -> None:
        resp = client.post(
            "/api/auth/compliance/pii-scan",
            json={"text": "write to jane.doe@example.com today"},
            headers=_headers(),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"] == {"EMAIL": 1}
        assert body["total"] == 1
        assert "jane.doe@example.com" not in body["preview"]
        assert "[REDACTED:EMAIL]" in body["preview"]
        assert opened == []

    def test_blank_text_is_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/compliance/pii-scan",
            json={"text": "   "},
            headers=_headers(),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "text is required"
