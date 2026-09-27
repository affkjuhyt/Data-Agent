"""HTTP-level tests for backend/services/auth_api/routers/ip_allowlist.py.

The router is mounted alone on a FastAPI app and driven through TestClient, so
the real `require_role` -> `get_current_user` chain decides who gets in. The
database is a scripted fake connection (one canned result per `execute`) and
the Kong sync is a recorder: what matters here is that the tenant from the
header reaches every WHERE/INSERT, that failures roll back instead of commit,
and that Kong is only re-synced after a change actually landed.
"""
from __future__ import annotations

import datetime
from typing import Any

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

ip_allowlist = load_module_from_path(
    "ip_allowlist_router",
    SERVICES / "auth_api" / "routers" / "ip_allowlist.py",
    extra_syspath=[SERVICES, SERVICES / "auth_api"],
)

_GATEWAY_KEY = "gw-test-key"
_TENANT = "tenant-a"
_NOW = datetime.datetime(2026, 9, 27, 8, 0, tzinfo=datetime.UTC)


class _Result:
    def __init__(
        self,
        rows: list[Any] | None = None,
        *,
        rowcount: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.rows = rows or []
        self.rowcount = len(self.rows) if rowcount is None else rowcount
        self.error = error


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._result = _Result()
        self.rowcount = -1

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))
        self._result = self._conn.script.pop(0) if self._conn.script else _Result()
        if self._result.error is not None:
            raise self._result.error
        self.rowcount = self._result.rowcount

    def fetchone(self) -> Any:
        return self._result.rows[0] if self._result.rows else None

    def fetchall(self) -> list[Any]:
        return list(self._result.rows)


class _Conn:
    def __init__(self, *script: _Result) -> None:
        self.script = list(script)
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, cursor_factory: Any = None) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _rule(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "cidr": "10.0.0.0/8",
        "label": "office",
        "is_active": True,
        "created_by": "user-1",
        "created_at": _NOW,
    }
    row.update(overrides)
    return row


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
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ip_allowlist.router)
    return TestClient(app, raise_server_exceptions=False)


def _use_db(monkeypatch: pytest.MonkeyPatch, conn: _Conn) -> None:
    monkeypatch.setattr(ip_allowlist, "get_db", lambda: conn)


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
    def test_request_not_marked_by_kong_is_401(self, client: TestClient, synced: list[str]) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 401

    def test_wrong_gateway_key_is_401(self, client: TestClient, synced: list[str]) -> None:
        # The marker alone is forgeable; the key is what proves Kong sent it.
        headers = _headers(**{"X-Gateway-Key": "not-the-key"})
        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 401

    def test_tenant_admin_cannot_change_the_platform_mode(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/ip-allowlist/config", json={"mode": "allow_all"},
            headers=_headers(roles="tenant-admin"),
        )

        assert resp.status_code == 403
        assert conn.executed == []
        assert synced == []


class TestConfig:
    def test_get_reports_the_stored_mode(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ip_allowlist, "get_ip_allowlist_mode", lambda: "allowlist")

        resp = client.get("/api/auth/ip-allowlist/config", headers=_headers())

        assert resp.status_code == 200
        assert resp.json() == {"mode": "allowlist"}

    def test_put_persists_mode_then_resyncs_kong(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/ip-allowlist/config", json={"mode": "allow_all"}, headers=_headers(),
        )

        assert resp.status_code == 200
        assert resp.json() == {"mode": "allow_all"}
        [(sql, params)] = conn.executed
        assert "platform_settings" in sql
        assert params == ("allow_all",)
        assert conn.commits == 1 and conn.closed
        assert synced == [_TENANT]

    def test_put_unknown_mode_is_422_and_touches_nothing(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/ip-allowlist/config", json={"mode": "deny_all"}, headers=_headers(),
        )

        assert resp.status_code == 422
        assert conn.executed == []
        assert synced == []


class TestList:
    def test_lists_only_the_header_tenant(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result([_rule(label=None)]))
        _use_db(monkeypatch, conn)

        resp = client.get("/api/auth/ip-allowlists", headers=_headers())

        assert resp.status_code == 200
        [(sql, params)] = conn.executed
        assert "WHERE tenant_id = %s" in sql
        assert params == (_TENANT,)
        assert resp.json() == [{
            "id": "11111111-1111-1111-1111-111111111111",
            "cidr": "10.0.0.0/8",
            "label": "",
            "is_active": True,
            "created_by": "user-1",
            "created_at": _NOW.isoformat(),
        }]

    def test_missing_tenant_header_is_422(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)
        headers = _headers()
        del headers["X-Tenant-Id"]

        assert client.get("/api/auth/ip-allowlists", headers=headers).status_code == 422
        assert conn.executed == []


class TestCreate:
    def test_inserts_for_header_tenant_and_actor(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result([_rule(cidr="192.168.1.0/24", label="vpn")]))
        _use_db(monkeypatch, conn)

        resp = client.post(
            "/api/auth/ip-allowlists",
            json={"cidr": "192.168.1.0/24", "label": "vpn"},
            headers=_headers(),
        )

        assert resp.status_code == 201
        assert resp.json()["cidr"] == "192.168.1.0/24"
        [(sql, params)] = conn.executed
        assert sql.startswith("INSERT INTO ip_allowlists")
        assert params == (_TENANT, "192.168.1.0/24", "vpn", True, "user-1")
        assert conn.commits == 1
        assert synced == [_TENANT]

    def test_invalid_cidr_is_422_before_any_write(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.post(
            "/api/auth/ip-allowlists", json={"cidr": "10.0.0.300/8"}, headers=_headers(),
        )

        assert resp.status_code == 422
        assert "Invalid CIDR" in resp.json()["detail"]
        assert conn.executed == []
        assert synced == []

    def test_duplicate_cidr_is_409_rolled_back_and_not_synced(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result(error=psycopg2.errors.UniqueViolation()))
        _use_db(monkeypatch, conn)

        resp = client.post(
            "/api/auth/ip-allowlists", json={"cidr": "10.0.0.0/8"}, headers=_headers(),
        )

        assert resp.status_code == 409
        assert conn.rollbacks == 1 and conn.commits == 0
        assert synced == []


class TestToggleAndDelete:
    def test_toggle_is_scoped_to_header_tenant(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result([_rule(is_active=False)]))
        _use_db(monkeypatch, conn)

        resp = client.patch("/api/auth/ip-allowlists/rule-9/toggle", headers=_headers())

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
        [(_sql, params)] = conn.executed
        assert params == ("rule-9", _TENANT)
        assert conn.commits == 1
        assert synced == [_TENANT]

    def test_toggle_of_another_tenants_rule_is_404(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The tenant filter makes a foreign rule indistinguishable from a
        # missing one: UPDATE ... RETURNING yields no row.
        conn = _Conn(_Result([]))
        _use_db(monkeypatch, conn)

        resp = client.patch("/api/auth/ip-allowlists/rule-9/toggle", headers=_headers())

        assert resp.status_code == 404
        assert conn.rollbacks == 1 and conn.commits == 0
        assert synced == []

    def test_delete_commits_and_resyncs(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result(rowcount=1))
        _use_db(monkeypatch, conn)

        resp = client.delete("/api/auth/ip-allowlists/rule-9", headers=_headers())

        assert resp.status_code == 204
        [(sql, params)] = conn.executed
        assert "tenant_id = %s" in sql
        assert params == ("rule-9", _TENANT)
        assert conn.commits == 1
        assert synced == [_TENANT]

    def test_delete_of_unknown_rule_is_404(
        self, client: TestClient, synced: list[str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(_Result(rowcount=0))
        _use_db(monkeypatch, conn)

        resp = client.delete("/api/auth/ip-allowlists/rule-9", headers=_headers())

        assert resp.status_code == 404
        assert conn.rollbacks == 1 and conn.commits == 0
        assert synced == []
