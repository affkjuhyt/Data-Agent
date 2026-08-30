"""Governance Catalog read endpoints (sandbox_schema_docs.docx §2.6) — bare
FastAPI app with only routers/schema_catalog.router mounted, same pattern
as test_workflow_schema_memory.py's schema_memory_client fixture. Real
Postgres testcontainer; no Qdrant/Kafka/Mongo needed since this router
only reads SchemaCatalog rows directly.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid
from typing import Iterator

import psycopg2
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute("DROP ROLE IF EXISTS app_user")
    finally:
        conn.close()
    yield postgres_container


@pytest.fixture
def migrated_db(fresh_db) -> dict[str, str]:
    result = _run_alembic(fresh_db["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return fresh_db


def _seed_tenant_warehouse_and_catalog_row(dsn: str) -> tuple[str, str, str]:
    tenant_id, warehouse_id, catalog_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "Router Test Tenant", f"router-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "KBWarehouse" (warehouse_id, tenant_id, service, description)
                   VALUES (%s, %s, 'Snowflake', 'test warehouse')""",
                (warehouse_id, tenant_id),
            )
            import json as _json
            cur.execute(
                """INSERT INTO "SchemaCatalog"
                       (id, tenant_id, warehouse_id, schema_name, table_name, columns, row_count,
                        text_description, join_hints, schema_hash)
                   VALUES (%s, %s, %s, 'ANALYTICS', 'fact_stock_price', %s::jsonb, 4820000,
                           'OHLCV stock price', %s::jsonb, 'abc123')""",
                (catalog_id, tenant_id, warehouse_id,
                 _json.dumps(["ticker", "close"]), _json.dumps(["dim_company.ticker"])),
            )
    finally:
        conn.close()
    return tenant_id, warehouse_id, catalog_id


@pytest.fixture
def schema_catalog_client(migrated_db):
    svc_dir = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
    added = [p for p in (svc_dir,) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib as _il
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from libs.database_connector.postgres_connector import client as pg_client

        schema_catalog_mod = _il.import_module("routers.schema_catalog")
        _il.reload(schema_catalog_mod)

        app = FastAPI()
        app.include_router(schema_catalog_mod.router)

        @app.on_event("startup")
        async def _startup():
            pg_client.set_url(migrated_db["dsn"])
            await pg_client.open()

        @app.on_event("shutdown")
        async def _shutdown():
            await pg_client.close()

        with TestClient(app) as tc:
            yield tc, migrated_db["dsn"]
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def test_list_schema_catalog_returns_seeded_row(schema_catalog_client):
    tc, dsn = schema_catalog_client
    tenant_id, warehouse_id, catalog_id = _seed_tenant_warehouse_and_catalog_row(dsn)

    resp = tc.get("/api/schema-catalog", headers={"X-Tenant-ID": tenant_id})

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == catalog_id
    assert rows[0]["table_name"] == "fact_stock_price"
    assert rows[0]["row_count"] == 4820000
    assert rows[0]["join_hints"] == ["dim_company.ticker"]


def test_list_schema_catalog_filters_by_warehouse_id(schema_catalog_client):
    tc, dsn = schema_catalog_client
    tenant_id, warehouse_id, _ = _seed_tenant_warehouse_and_catalog_row(dsn)

    resp = tc.get("/api/schema-catalog", headers={"X-Tenant-ID": tenant_id},
                  params={"warehouse_id": str(uuid.uuid4())})

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_schema_catalog_isolates_tenants(schema_catalog_client):
    tc, dsn = schema_catalog_client
    _seed_tenant_warehouse_and_catalog_row(dsn)
    other_tenant_id = str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
            (other_tenant_id, "Other Tenant", f"other-{other_tenant_id[:8]}", "Asia-SE1"),
        )
    conn.close()

    resp = tc.get("/api/schema-catalog", headers={"X-Tenant-ID": other_tenant_id})

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_schema_catalog_entry_by_id(schema_catalog_client):
    tc, dsn = schema_catalog_client
    tenant_id, _, catalog_id = _seed_tenant_warehouse_and_catalog_row(dsn)

    resp = tc.get(f"/api/schema-catalog/{catalog_id}", headers={"X-Tenant-ID": tenant_id})

    assert resp.status_code == 200
    assert resp.json()["table_name"] == "fact_stock_price"


def test_get_schema_catalog_entry_unknown_id_returns_404(schema_catalog_client):
    tc, dsn = schema_catalog_client
    tenant_id, _, _ = _seed_tenant_warehouse_and_catalog_row(dsn)

    resp = tc.get(f"/api/schema-catalog/{uuid.uuid4()}", headers={"X-Tenant-ID": tenant_id})

    assert resp.status_code == 404
