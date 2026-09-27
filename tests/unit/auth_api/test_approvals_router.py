"""HTTP-level tests for backend/services/auth_api/routers/approvals.py, the
Microsoft SSO first-login approval gate.

The router has no `require_role`: every endpoint takes `get_current_user` and
does its own role/tenant check, so these tests drive that real dependency with
Kong headers and assert who may list, approve and reject. Keycloak calls are
replaced by recorders and the database by a scripted fake connection; the
properties under test are the tenant scoping of a tenant-admin, and that the DB
is never committed when the Keycloak update fails.
"""
from __future__ import annotations

import datetime
import sys
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

def _load_approvals() -> ModuleType:
    """Load approvals.py the way the image resolves it (only backend/ on the path).

    Unlike its sibling routers it imports `services.auth_api.auth_core` with no
    `auth_api.*` fallback. Other auth_api tests put backend/services/auth_api on
    sys.path, where the service's own `services/` subpackage (kong_sync, ...)
    shadows the top-level `services` package, and a failed import leaves that
    wrong package cached. Both are set aside for this load and put back after,
    so the other suites keep resolving imports exactly as before.
    """
    shadow = str(SERVICES / "auth_api")
    cached = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "services" or name.startswith("services.")
    }
    saved_path = list(sys.path)
    sys.path[:] = [entry for entry in sys.path if entry != shadow]
    try:
        return load_module_from_path("approvals_router", SERVICES / "auth_api" / "routers" / "approvals.py")
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "services" or n.startswith("services.")]:
            del sys.modules[name]
        sys.modules.update(cached)


approvals = _load_approvals()

_GATEWAY_KEY = "gw-test-key"
_TENANT = "tenant-a"
_OTHER_TENANT = "tenant-b"
_NOW = datetime.datetime(2026, 9, 27, 8, 0, tzinfo=datetime.UTC)


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._rows: list[Any] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))
        self._rows = self._conn.script.pop(0) if self._conn.script else []

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Conn:
    """Each `execute` consumes the next scripted list of rows."""

    def __init__(self, *script: list[Any]) -> None:
        self.script = list(script)
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.closed = False

    def cursor(self, cursor_factory: Any = None) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Keycloak:
    """Records the Keycloak side effects the router triggers."""

    def __init__(self, *, is_ms_user: bool = True, assign_error: Exception | None = None) -> None:
        self.is_ms_user = is_ms_user
        self.assign_error = assign_error
        self.enabled: list[tuple[str, bool]] = []
        self.assigned: list[tuple[str, str, str]] = []

    def is_microsoft_sso_user(self, user_id: str) -> tuple[bool, str, str]:
        if self.is_ms_user:
            return True, "jane@example.com", "Jane Doe"
        return False, "", ""

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        self.enabled.append((user_id, enabled))

    def assign(self, user_id: str, role: str, tenant_id: str) -> None:
        if self.assign_error is not None:
            raise self.assign_error
        self.assigned.append((user_id, role, tenant_id))


def _pending(**overrides: Any) -> dict[str, Any]:
    row = {
        "keycloak_user_id": "sso-user",
        "email": "jane@example.com",
        "display_name": "Jane Doe",
        "requested_role": "data-analyst",
        "requested_tenant_id": _TENANT,
        "status": "pending",
        "created_at": _NOW,
    }
    row.update(overrides)
    return row


def _headers(roles: str = "platform-admin", tenant: str = _TENANT, **overrides: str) -> dict[str, str]:
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


def _install(monkeypatch: pytest.MonkeyPatch, conn: _Conn, kc: _Keycloak | None = None) -> _Keycloak:
    kc = kc or _Keycloak()
    monkeypatch.setattr(approvals, "get_db", lambda: conn)
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
    def test_request_not_marked_by_kong_is_401(self, client: TestClient) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_wrong_gateway_key_is_401(self, client: TestClient) -> None:
        headers = _headers(**{"X-Gateway-Key": "forged"})
        assert client.get("/api/auth/approvals", headers=headers).status_code == 401

    def test_non_admin_cannot_list_approvals(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _install(monkeypatch, conn)

        resp = client.get("/api/auth/approvals", headers=_headers(roles="data-analyst"))

        assert resp.status_code == 403
        assert conn.executed == []


class TestMe:
    def test_approved_user_is_active(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn([_pending(status="approved")])
        _install(monkeypatch, conn)

        resp = client.get("/api/auth/me", headers=_headers(roles="", **{"X-User-Id": "sso-user"}))

        assert resp.json() == {"status": "active"}
        assert conn.executed[0][1] == ("sso-user",)

    def test_pending_user_is_gated(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, _Conn([_pending()]))

        resp = client.get("/api/auth/me", headers=_headers(roles="", **{"X-User-Id": "sso-user"}))

        assert resp.json() == {"status": "pending_approval", "since": _NOW.isoformat()}

    def test_unknown_sso_user_must_submit_profile(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch, _Conn([]))

        resp = client.get("/api/auth/me", headers=_headers(roles=""))

        assert resp.json() == {"status": "needs_profile"}


class TestSubmitProfile:
    def test_creates_pending_record_and_disables_account(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([{"status": "pending", "created_at": _NOW}])
        kc = _install(monkeypatch, conn)

        resp = client.post(
            "/api/auth/me/profile",
            json={"requested_role": "data-analyst", "requested_tenant_id": f"  {_TENANT} "},
            headers=_headers(roles="", **{"X-User-Id": "sso-user"}),
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "pending_approval", "since": _NOW.isoformat()}
        [(sql, params)] = conn.executed
        assert sql.startswith("INSERT INTO microsoft_sso_approvals")
        assert params == ("sso-user", "jane@example.com", "Jane Doe", "data-analyst", _TENANT)
        assert conn.commits == 1
        assert kc.enabled == [("sso-user", False)]

    def test_platform_admin_role_cannot_be_requested(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        kc = _install(monkeypatch, conn)

        resp = client.post(
            "/api/auth/me/profile",
            json={"requested_role": "platform-admin", "requested_tenant_id": _TENANT},
            headers=_headers(roles=""),
        )

        assert resp.status_code == 422
        assert conn.executed == []
        assert kc.enabled == []

    def test_non_sso_user_is_403(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn()
        _install(monkeypatch, conn, _Keycloak(is_ms_user=False))

        resp = client.post(
            "/api/auth/me/profile",
            json={"requested_role": "data-analyst", "requested_tenant_id": _TENANT},
            headers=_headers(roles=""),
        )

        assert resp.status_code == 403
        assert conn.executed == []


class TestListApprovals:
    def test_tenant_admin_sees_only_own_tenant(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([_pending()])
        _install(monkeypatch, conn)

        resp = client.get("/api/auth/approvals", headers=_headers(roles="tenant-admin"))

        assert resp.status_code == 200
        assert [r["keycloak_user_id"] for r in resp.json()] == ["sso-user"]
        [(sql, params)] = conn.executed
        assert "requested_tenant_id = %s" in sql
        assert params == ("pending", _TENANT)

    def test_platform_admin_all_is_unfiltered(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([])
        _install(monkeypatch, conn)

        resp = client.get("/api/auth/approvals?status=all", headers=_headers())

        assert resp.status_code == 200
        [(sql, params)] = conn.executed
        assert "WHERE" not in sql
        assert params is None

    def test_unknown_status_is_422(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn()
        _install(monkeypatch, conn)

        resp = client.get("/api/auth/approvals?status=deleted", headers=_headers())

        assert resp.status_code == 422
        assert conn.executed == []


class TestApproveReject:
    def test_platform_admin_approves_and_keycloak_gets_requested_role(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([_pending()], [])
        kc = _install(monkeypatch, conn)

        resp = client.post("/api/auth/approvals/sso-user/approve", headers=_headers(tenant=_OTHER_TENANT))

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "user_id": "sso-user", "status": "approved"}
        assert kc.assigned == [("sso-user", "data-analyst", _TENANT)]
        update_sql, update_params = conn.executed[1]
        assert "SET status = 'approved'" in update_sql
        assert update_params == ("admin-1", "sso-user")
        assert conn.commits == 1

    def test_tenant_admin_of_other_tenant_cannot_approve(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([_pending()])
        kc = _install(monkeypatch, conn)

        resp = client.post(
            "/api/auth/approvals/sso-user/approve",
            headers=_headers(roles="tenant-admin", tenant=_OTHER_TENANT),
        )

        assert resp.status_code == 403
        assert kc.assigned == []
        assert conn.commits == 0

    def test_keycloak_failure_leaves_request_pending(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Keycloak is updated before the DB so a failure there must not leave
        # the row marked approved while the account stays disabled.
        conn = _Conn([_pending()], [])
        _install(monkeypatch, conn, _Keycloak(assign_error=RuntimeError("keycloak down")))

        resp = client.post("/api/auth/approvals/sso-user/approve", headers=_headers())

        assert resp.status_code == 500
        assert conn.commits == 0
        assert not any("SET status" in sql for sql, _ in conn.executed)
        assert conn.closed

    def test_approve_without_pending_request_is_404(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([])
        kc = _install(monkeypatch, conn)

        resp = client.post("/api/auth/approvals/ghost/approve", headers=_headers())

        assert resp.status_code == 404
        assert kc.assigned == []
        assert conn.commits == 0

    def test_tenant_admin_rejects_own_tenant_request(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn([{"requested_tenant_id": _TENANT}], [])
        _install(monkeypatch, conn)

        resp = client.post(
            "/api/auth/approvals/sso-user/reject", headers=_headers(roles="tenant-admin"),
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        update_sql, update_params = conn.executed[1]
        assert "SET status = 'rejected'" in update_sql
        assert update_params == ("admin-1", "sso-user")
        assert conn.commits == 1
