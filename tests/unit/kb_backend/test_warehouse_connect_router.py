"""POST /api/knowledge/warehouses/{connect,select-tables} (router/knowledge/warehouse_connect.py).

Authorisation runs through the real require_perm/authenticate chain; Snowflake
and Postgres are faked at the router's own seams. Pinned: who may connect a
warehouse, that a failed Snowflake login is a 400 carrying its reason, that the
saved warehouse belongs to the caller's tenant, and that the password the user
typed is never written to the stored config.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.kb_backend.dependencies.auth as auth_mod
from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.permissions import PermissionStore
from services.kb_backend.router.knowledge import warehouse_connect

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-0000000000cc"
GATEWAY_KEY = "warehouse-test-gateway-key"
PASSWORD = "s3cret-typed-into-the-form"

CONNECT_BODY = {
    "account_identifier": "abc12345.us-east-1",
    "user": "loader",
    "password": PASSWORD,
    "warehouse": "COMPUTE_WH",
    "database": "SALES",
    "schema": "PUBLIC",
}
SELECT_BODY = {
    **CONNECT_BODY,
    "connection_name": "Sales DW",
    "warehouse_type": "Snowflake",
    "selected_table_ids": ["ORDERS", "GHOST"],
}


class _Snowflake:
    """Stands in for SnowflakeConnector; behaviour is set per test on the class."""

    result: ClassVar[dict[str, Any]] = {}
    schema_error: ClassVar[Exception | None] = None
    instances: ClassVar[list[_Snowflake]] = []

    def __init__(self) -> None:
        self.connect_kwargs: dict[str, Any] | None = None
        self.closed = False
        _Snowflake.instances.append(self)

    async def validate_and_connect(self, **kwargs: Any) -> dict[str, Any]:
        self.connect_kwargs = kwargs
        return _Snowflake.result

    async def get_table_schema(self, table_name: str) -> dict[str, Any]:
        if _Snowflake.schema_error is not None:
            raise _Snowflake.schema_error
        if table_name == "GHOST":
            return {}
        return {"table_name": table_name, "columns": [{"name": "ID", "type": "NUMBER"}]}

    def close(self) -> None:
        self.closed = True


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, grants: list[tuple[str, str]] | None = None) -> None:
        self._grants = grants or []
        self.commits = 0

    async def execute(self, _stmt: Any) -> _Rows:
        return _Rows(self._grants)

    async def commit(self) -> None:
        self.commits += 1


class _Postgres:
    def __init__(self, grants: list[tuple[str, str]]) -> None:
        self.grants = grants
        self.inserted: list[Any] = []
        self.session_tenants: list[str | None] = []
        self.session = _Session()
        self.fail_config_insert = False

    @asynccontextmanager
    async def get_platform_client(self) -> Any:
        yield _Session(self.grants)

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None) -> Any:
        self.session_tenants.append(tenant_id)
        yield self.session

    async def insert(self, data: Any, session: Any = None) -> ResponseModel[Any]:
        assert session is self.session, "both rows must share the request's transaction"
        self.inserted.append(data)
        if type(data).__name__ == "KBWarehouseInsert":
            return ResponseModel(code=200, data={"warehouse_id": "wh-1"})
        if self.fail_config_insert:
            return ResponseModel(code=500, error="boom")
        return ResponseModel(code=200, data={"config_id": "cfg-1"})


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    # A fresh store so grants cached by an earlier test cannot leak in.
    monkeypatch.setattr(auth_mod, "store", PermissionStore())
    monkeypatch.setattr(warehouse_connect, "SnowflakeConnector", _Snowflake)
    monkeypatch.setattr(_Snowflake, "instances", [])
    monkeypatch.setattr(_Snowflake, "schema_error", None)
    monkeypatch.setattr(_Snowflake, "result", {
        "success": True,
        "message": "Connected to SALES.PUBLIC",
        "tables": [{"table_id": "ORDERS", "table_name": "ORDERS", "row_count": 1234567}],
    })

    postgres = _Postgres(grants=[("PLATFORM_ADMIN", "addWarehouse"), ("BUSINESS_OPERATOR", "viewWarehouses")])
    app = FastAPI()
    app.include_router(warehouse_connect.router)
    app.state.postgres = postgres
    return SimpleNamespace(client=TestClient(app), postgres=postgres)


def _headers(role: str = "platform-admin") -> dict[str, str]:
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Id": "u-7",
        "X-User-Name": "alice",
        "X-User-Roles": role,
        "X-Tenant-Id": TENANT,
    }


def test_routes_are_registered() -> None:
    routes = {(m, r.path) for r in warehouse_connect.router.routes for m in r.methods}
    assert ("POST", "/api/knowledge/warehouses/connect") in routes
    assert ("POST", "/api/knowledge/warehouses/select-tables") in routes


class TestGuard:
    def test_unverified_caller_is_401(self, env: SimpleNamespace) -> None:
        resp = env.client.post("/api/knowledge/warehouses/connect", json=CONNECT_BODY)

        assert resp.status_code == 401
        assert _Snowflake.instances == []

    @pytest.mark.parametrize("path, body", [
        ("/api/knowledge/warehouses/connect", CONNECT_BODY),
        ("/api/knowledge/warehouses/select-tables", SELECT_BODY),
    ])
    def test_role_that_may_only_view_warehouses_is_403(self, env: SimpleNamespace, path: str, body: dict) -> None:
        resp = env.client.post(path, json=body, headers=_headers(role="business-operator"))

        assert resp.status_code == 403
        assert "addWarehouse" in resp.json()["detail"]
        # Refused before any login attempt is made with the submitted credentials.
        assert _Snowflake.instances == []
        assert env.postgres.inserted == []


class TestConnect:
    def test_successful_login_lists_tables_and_closes_the_connection(self, env: SimpleNamespace) -> None:
        resp = env.client.post("/api/knowledge/warehouses/connect", json=CONNECT_BODY, headers=_headers())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is True and data["message"] == "Connected to SALES.PUBLIC"
        assert [t["table_name"] for t in data["tables"]] == ["ORDERS"]
        assert [s.closed for s in _Snowflake.instances] == [True]

    def test_failed_login_is_400_with_the_reason(self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_Snowflake, "result", {
            "success": False, "message": "Connection failed: bad password", "tables": [],
        })

        resp = env.client.post("/api/knowledge/warehouses/connect", json=CONNECT_BODY, headers=_headers())

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Connection failed: bad password"
        assert [s.closed for s in _Snowflake.instances] == [True]

    def test_missing_required_field_is_422(self, env: SimpleNamespace) -> None:
        body = {k: v for k, v in CONNECT_BODY.items() if k != "password"}

        resp = env.client.post("/api/knowledge/warehouses/connect", json=body, headers=_headers())

        assert resp.status_code == 422
        assert _Snowflake.instances == []


class TestSelectTables:
    def test_saves_the_warehouse_for_the_callers_tenant_without_the_password(self, env: SimpleNamespace) -> None:
        resp = env.client.post("/api/knowledge/warehouses/select-tables", json=SELECT_BODY, headers=_headers())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["warehouse_id"] == "wh-1"
        assert data["warehouse_type"] == "snowflake"
        assert data["message"] == "Warehouse connected with 2 table(s)"
        orders, ghost = data["tables"]
        assert orders["rowCount"] == "1,234,567" and orders["columns"] == [{"name": "ID", "type": "NUMBER"}]
        # A selected table the discovery did not return still saves, marked unknown.
        assert ghost["rowCount"] == "—" and ghost["columns"] == []

        warehouse, config = env.postgres.inserted
        assert env.postgres.session_tenants == [TENANT]
        assert warehouse.tenant_id == TENANT
        assert config.warehouse_id == "wh-1" and config.created_by == "alice"
        assert PASSWORD not in config.model_dump_json()
        assert env.postgres.session.commits == 1

    def test_failed_config_insert_is_500_and_nothing_is_committed(self, env: SimpleNamespace) -> None:
        env.postgres.fail_config_insert = True

        resp = env.client.post("/api/knowledge/warehouses/select-tables", json=SELECT_BODY, headers=_headers())

        assert resp.status_code == 500
        # The warehouse row was only flushed; without a commit it rolls back with the config.
        assert env.postgres.session.commits == 0

    def test_failed_login_is_400_and_nothing_is_saved(self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_Snowflake, "result", {"success": False, "message": "Connection failed: x", "tables": []})

        resp = env.client.post("/api/knowledge/warehouses/select-tables", json=SELECT_BODY, headers=_headers())

        assert resp.status_code == 400
        assert env.postgres.inserted == []
        assert [s.closed for s in _Snowflake.instances] == [True]

    def test_schema_read_failure_is_502_and_nothing_is_saved(
            self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_Snowflake, "schema_error", RuntimeError("insufficient privileges"))

        resp = env.client.post("/api/knowledge/warehouses/select-tables", json=SELECT_BODY, headers=_headers())

        assert resp.status_code == 502
        assert "insufficient privileges" in resp.json()["detail"]
        assert env.postgres.inserted == []

    def test_selected_tables_must_be_a_list(self, env: SimpleNamespace) -> None:
        resp = env.client.post(
            "/api/knowledge/warehouses/select-tables",
            json={**SELECT_BODY, "selected_table_ids": "ORDERS"},
            headers=_headers(),
        )

        assert resp.status_code == 422
        assert env.postgres.inserted == []
