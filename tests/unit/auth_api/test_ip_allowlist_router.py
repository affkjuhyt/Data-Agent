"""HTTP-level tests for backend/services/auth_api/routers/ip_allowlist.py — the
checks that must refuse a request before it reaches the database or Kong.

The router is mounted alone on a FastAPI app and driven through TestClient, so
the real `require_role` -> `get_current_user` chain decides who gets in.
`get_session` is a tripwire that fails the request if it is opened, and the Kong
sync is a recorder. Tenant scoping of reads and writes, rollback of a failed
write and re-syncing Kong only after a change landed are in
tests/integration/auth_api/test_ip_allowlist_router.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth_api.routers import ip_allowlist

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


@pytest.fixture
def synced(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Kong re-syncs recorded by tenant; also pins the gateway key so the
    real trust check runs instead of the unset-key fallback."""
    monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(ip_allowlist, "sync_kong_ip_restrictions", calls.append)
    return calls


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every attempt to open a DB session; each one also fails the request."""
    calls: list[str] = []

    def _tripwire() -> Any:
        calls.append("get_session")
        raise AssertionError("the request reached the database")

    monkeypatch.setattr(ip_allowlist, "get_session", _tripwire)
    return calls


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ip_allowlist.router)
    return TestClient(app, raise_server_exceptions=False)


def test_routes_are_registered() -> None:
    registered = {
        (method, route.path)
        for route in ip_allowlist.router.routes
        for method in getattr(route, "methods", ())
    }
    assert registered >= {
        ("GET", "/api/auth/ip-allowlist/config"),
        ("PUT", "/api/auth/ip-allowlist/config"),
        ("GET", "/api/auth/ip-allowlists"),
        ("POST", "/api/auth/ip-allowlists"),
        ("PATCH", "/api/auth/ip-allowlists/{rule_id}/toggle"),
        ("DELETE", "/api/auth/ip-allowlists/{rule_id}"),
    }


class TestAuthGuard:
    def test_request_not_marked_by_kong_is_401(
        self,
        client: TestClient,
        synced: list[str],
        opened: list[str],
    ) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 401
        assert opened == []

    def test_wrong_gateway_key_is_401(
        self, client: TestClient, synced: list[str], opened: list[str]
    ) -> None:
        # The marker alone is forgeable; the key is what proves Kong sent it.
        headers = _headers(**{"X-Gateway-Key": "not-the-key"})
        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 401
        assert opened == []

    def test_tenant_admin_cannot_change_the_platform_mode(
        self,
        client: TestClient,
        synced: list[str],
        opened: list[str],
    ) -> None:
        resp = client.put(
            "/api/auth/ip-allowlist/config",
            json={"mode": "allow_all"},
            headers=_headers(roles="tenant-admin"),
        )

        assert resp.status_code == 403
        assert opened == []
        assert synced == []


class TestConfig:
    def test_get_reports_the_stored_mode(
        self,
        client: TestClient,
        synced: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ip_allowlist, "get_ip_allowlist_mode", lambda: "allowlist")

        resp = client.get("/api/auth/ip-allowlist/config", headers=_headers())

        assert resp.status_code == 200
        assert resp.json() == {"mode": "allowlist"}

    def test_put_unknown_mode_is_422_and_touches_nothing(
        self,
        client: TestClient,
        synced: list[str],
        opened: list[str],
    ) -> None:
        resp = client.put(
            "/api/auth/ip-allowlist/config",
            json={"mode": "deny_all"},
            headers=_headers(),
        )

        assert resp.status_code == 422
        assert opened == []
        assert synced == []


class TestList:
    def test_missing_tenant_header_is_422(
        self, client: TestClient, synced: list[str], opened: list[str]
    ) -> None:
        headers = _headers()
        del headers["X-Tenant-Id"]

        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 422
        assert opened == []


class TestCreate:
    def test_invalid_cidr_is_422_before_any_write(
        self,
        client: TestClient,
        synced: list[str],
        opened: list[str],
    ) -> None:
        resp = client.post(
            "/api/auth/ip-allowlists",
            json={"cidr": "10.0.0.300/8"},
            headers=_headers(),
        )

        assert resp.status_code == 422
        assert "Invalid CIDR" in resp.json()["detail"]
        assert opened == []
        assert synced == []
