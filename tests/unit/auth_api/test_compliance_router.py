"""HTTP-level tests for backend/services/auth_api/routers/compliance.py.

`pii_masking` stored here is read by the KB worker at ingestion to decide
whether PII is redacted, so the settings write is an enforcement switch: these
tests pin that it lands under the caller's tenant, that input is normalised
before it is stored, and that only platform-admin can reach it. The router runs
behind the real `require_role` dependency; the database is a scripted fake.
"""
from __future__ import annotations

import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

compliance = load_module_from_path(
    "compliance_router",
    SERVICES / "auth_api" / "routers" / "compliance.py",
    extra_syspath=[SERVICES, SERVICES / "auth_api"],
)

_GATEWAY_KEY = "gw-test-key"
_TENANT = "tenant-a"
_NOW = datetime.datetime(2026, 9, 27, 8, 0, tzinfo=datetime.UTC)


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._row: Any = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((" ".join(sql.split()), params))
        self._row = self._conn.script.pop(0) if self._conn.script else None

    def fetchone(self) -> Any:
        return self._row


class _Conn:
    """Each `execute` consumes the next scripted row (None = no row)."""

    def __init__(self, *script: Any) -> None:
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


def _stored_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "tenant_id": _TENANT,
        "pii_masking": True,
        "pii_types": "EMAIL,PHONE",
        "residency_region": "Asia-SE1",
        "residency_standards": "GDPR",
        "retention_days": 30,
        "retention_lock": False,
        "zero_trust": False,
        "audit_residency": False,
        "updated_at": _NOW,
        "updated_by": "user-1",
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


@pytest.fixture(autouse=True)
def _gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A set key makes the real trust check run instead of the unset-key fallback.
    monkeypatch.setenv("GATEWAY_KEY", _GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(compliance.router)
    return TestClient(app, raise_server_exceptions=False)


def _use_db(monkeypatch: pytest.MonkeyPatch, conn: _Conn) -> None:
    monkeypatch.setattr(compliance, "get_db", lambda: conn)


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
    def test_request_not_marked_by_kong_is_401(self, client: TestClient) -> None:
        headers = _headers()
        del headers["X-Kong-Verified"]
        assert client.get("/api/auth/compliance/settings", headers=headers).status_code == 401

    def test_wrong_gateway_key_is_401(self, client: TestClient) -> None:
        headers = _headers(**{"X-Gateway-Key": "forged"})
        assert client.get("/api/auth/compliance/settings", headers=headers).status_code == 401

    def test_tenant_admin_cannot_switch_pii_masking(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/compliance/settings", json={"pii_masking": False},
            headers=_headers(roles="tenant-admin"),
        )

        assert resp.status_code == 403
        assert conn.executed == []


class TestSettings:
    def test_get_without_stored_row_returns_defaults_for_header_tenant(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(None)
        _use_db(monkeypatch, conn)

        resp = client.get("/api/auth/compliance/settings", headers=_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["pii_masking"] is False
        assert body["retention_days"] == 365
        assert body["updated_at"] is None
        [(_sql, params)] = conn.executed
        assert params == (_TENANT,)
        assert conn.closed

    def test_put_normalises_types_and_upserts_under_header_tenant(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # read current (none) -> upsert -> re-read what was stored
        conn = _Conn(None, None, _stored_row(pii_types="EMAIL", retention_days=30))
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/compliance/settings",
            json={"pii_masking": True, "pii_types": [" email ", "not-a-type"], "retention_days": 30},
            headers=_headers(),
        )

        assert resp.status_code == 200
        assert resp.json()["pii_types"] == ["EMAIL"]
        assert conn.commits == 1
        upserts = [(sql, p) for sql, p in conn.executed if sql.startswith("INSERT INTO compliance_settings")]
        [(_sql, params)] = upserts
        # tenant first, actor last; unknown PII types are dropped and the rest
        # upper-cased so the worker only ever sees names it can match.
        assert params[0] == _TENANT
        assert params[1] is True
        assert params[2] == "EMAIL"
        assert params[5] == 30
        assert params[-1] == "user-1"
        # Fields the body left out keep their current (default) values.
        assert params[3] == "Asia-SE1"

    def test_put_negative_retention_is_422_and_not_written(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn(None)
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/compliance/settings", json={"retention_days": -1}, headers=_headers(),
        )

        assert resp.status_code == 422
        assert not any(sql.startswith("INSERT") for sql, _ in conn.executed)
        assert conn.commits == 0
        assert conn.closed

    def test_put_with_wrong_body_type_is_422(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _Conn()
        _use_db(monkeypatch, conn)

        resp = client.put(
            "/api/auth/compliance/settings", json={"retention_days": "forever"}, headers=_headers(),
        )

        assert resp.status_code == 422
        assert conn.executed == []


class TestScore:
    def test_defaults_score_from_retention_and_residency_only(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _use_db(monkeypatch, _Conn(None))

        resp = client.get("/api/auth/compliance/score", headers=_headers())

        assert resp.status_code == 200
        body = resp.json()
        # protection 20 (retention>0, no lock), residency 100, access 0
        assert body["score"] == 38.0
        assert {p["id"]: p["status"] for p in body["policies"]}["pol-pii"] == "PENDING_SCAN"

    def test_every_control_on_scores_100(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = _stored_row(retention_lock=True, zero_trust=True, audit_residency=True)
        _use_db(monkeypatch, _Conn(row))

        body = client.get("/api/auth/compliance/score", headers=_headers()).json()

        assert body["score"] == 100.0
        assert all(p["status"] == "COMPLIANT" for p in body["policies"])


class TestPiiScan:
    def test_masks_without_echoing_the_raw_value(self, client: TestClient) -> None:
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

    def test_blank_text_is_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/compliance/pii-scan", json={"text": "   "}, headers=_headers(),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "text is required"
