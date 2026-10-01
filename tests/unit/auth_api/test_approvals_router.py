"""HTTP-level tests for backend/services/auth_api/routers/approvals.py, the
Microsoft SSO first-login approval gate — the checks that must refuse a request
before it reaches the database.

The router has no `require_role`: every endpoint takes `get_current_user` and
does its own role check, so these tests drive that real dependency with Kong
headers. Keycloak calls are replaced by recorders and `get_session` by a tripwire
that fails the request if it is opened. What depends on real rows (tenant
scoping, the upsert guard, the FOR UPDATE lock, rollback when Keycloak fails) is
in tests/integration/auth_api/test_approvals_router.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth_api.routers import approvals

pytestmark = pytest.mark.unit

_GATEWAY_KEY = "gw-test-key"
_TENANT = "tenant-a"


class _Keycloak:
    """Records the Keycloak side effects the router triggers."""

    def __init__(self, *, is_ms_user: bool = True) -> None:
        self.is_ms_user = is_ms_user
        self.enabled: list[tuple[str, bool]] = []
        self.assigned: list[tuple[str, str, str]] = []

    def is_microsoft_sso_user(self, user_id: str) -> tuple[bool, str, str]:
        if self.is_ms_user:
            return True, "jane@example.com", "Jane Doe"
        return False, "", ""

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        self.enabled.append((user_id, enabled))

    def assign(self, user_id: str, role: str, tenant_id: str) -> None:
        self.assigned.append((user_id, role, tenant_id))


def _headers(
    roles: str = "platform-admin", tenant: str = _TENANT, **overrides: str
) -> dict[str, str]:
    headers = {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": _GATEWAY_KEY,
        "X-User-Id": "admin-1",
        "X-User-Roles": roles,
        "X-Tenant-Id": tenant,
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

    monkeypatch.setattr(approvals, "get_session", _tripwire)
    return calls


def _install(monkeypatch: pytest.MonkeyPatch, kc: _Keycloak | None = None) -> _Keycloak:
    kc = kc or _Keycloak()
    monkeypatch.setattr(approvals, "_is_microsoft_sso_user", kc.is_microsoft_sso_user)
    monkeypatch.setattr(approvals, "_set_kc_user_enabled", kc.set_enabled)
    monkeypatch.setattr(approvals, "_assign_kc_user_role_tenant", kc.assign)
    return kc


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(approvals.router)
    return TestClient(app, raise_server_exceptions=False)


def test_routes_are_registered() -> None:
    registered = {
        (method, route.path)
        for route in approvals.router.routes
        for method in getattr(route, "methods", ())
    }
    assert registered >= {
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/me/profile"),
        ("GET", "/api/auth/me/roles"),
        ("GET", "/api/auth/me/tenants"),
        ("GET", "/api/auth/approvals"),
        ("POST", "/api/auth/approvals/{user_id}/approve"),
        ("POST", "/api/auth/approvals/{user_id}/reject"),
    }


class TestAuthGuard:
    def test_request_not_marked_by_kong_is_401(self, client: TestClient, opened: list[str]) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert opened == []

    def test_wrong_gateway_key_is_401(self, client: TestClient, opened: list[str]) -> None:
        headers = _headers(**{"X-Gateway-Key": "forged"})
        assert client.get("/api/auth/approvals", headers=headers).status_code == 401
        assert opened == []

    def test_non_admin_cannot_list_approvals(
        self,
        client: TestClient,
        opened: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch)

        resp = client.get("/api/auth/approvals", headers=_headers(roles="data-analyst"))

        assert resp.status_code == 403
        assert opened == []


class TestSubmitProfile:
    def test_platform_admin_role_cannot_be_requested(
        self,
        client: TestClient,
        opened: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        kc = _install(monkeypatch)

        resp = client.post(
            "/api/auth/me/profile",
            json={"requested_role": "platform-admin", "requested_tenant_id": _TENANT},
            headers=_headers(roles=""),
        )

        assert resp.status_code == 422
        assert opened == []
        assert kc.enabled == []

    def test_non_sso_user_is_403(
        self,
        client: TestClient,
        opened: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        kc = _install(monkeypatch, _Keycloak(is_ms_user=False))

        resp = client.post(
            "/api/auth/me/profile",
            json={"requested_role": "data-analyst", "requested_tenant_id": _TENANT},
            headers=_headers(roles=""),
        )

        assert resp.status_code == 403
        assert opened == []
        assert kc.enabled == []


class TestListApprovals:
    def test_unknown_status_is_422(
        self,
        client: TestClient,
        opened: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch)

        resp = client.get("/api/auth/approvals?status=deleted", headers=_headers())

        assert resp.status_code == 422
        assert opened == []
