"""Integration tests for the Schema Crawler Worker (sandbox_schema_docs.docx
§II) — real Postgres + Qdrant testcontainers, real SchemaCatalog migration.
Snowflake discovery and the LLM-description call are monkeypatched (see
"Mock for now" decision in docs/Sandbox_SchemaCrawler_Implementation.md) —
no real warehouse/LLM provider is contacted anywhere in this file. embed()
is stubbed to a fixed-but-distinguishable vector, same technique as
test_workflow_schema_memory.py's `wm` fixture.
"""
from __future__ import annotations

import importlib
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
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "schema-crawler-worker")


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


def _seed_tenant_and_warehouse(dsn: str, selected_tables: list[str] | None = None) -> tuple[str, str]:
    tenant_id, warehouse_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "Crawler Test Tenant", f"crawler-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "KBWarehouse" (warehouse_id, tenant_id, service, description)
                   VALUES (%s, %s, 'Snowflake', 'test warehouse')""",
                (warehouse_id, tenant_id),
            )
            import json as _json
            config = {
                "connection": {
                    "account": "test-account", "username": "test-user",
                    "warehouse": "TEST_WH", "database": "TEST_DB", "schema": "ANALYTICS", "role": None,
                },
                "selected_tables": [{"name": t} for t in (selected_tables or [])],
            }
            cur.execute(
                """INSERT INTO "KBWarehouseConfig" (config_id, warehouse_id, version_number, is_active, config)
                   VALUES (%s, %s, 1, true, %s::jsonb)""",
                (str(uuid.uuid4()), warehouse_id, _json.dumps(config)),
            )
    finally:
        conn.close()
    return tenant_id, warehouse_id


_FAKE_METADATA = {
    "fact_stock_price": {
        "schema_name": "ANALYTICS",
        "table_name": "fact_stock_price",
        "columns": [
            {"name": "ticker", "type": "VARCHAR", "nullable": False, "comment": None},
            {"name": "close", "type": "FLOAT", "nullable": False, "comment": None},
        ],
        "row_count": 4820000,
        "last_altered": None,
    },
}


@pytest.fixture
def worker(migrated_db, qdrant_container, monkeypatch):
    """Imports backend/services/schema-crawler-worker/main.py the way the container
    does (top-level import, service dir on sys.path), pointed at the
    migrated test DB + test Qdrant, with Snowflake discovery and the LLM
    describe/embed calls stubbed."""
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.setenv("QDRANT_URL", qdrant_container["url"])
    monkeypatch.setenv("DB_HOST", "unused")  # main() isn't called; wait_for_postgres() isn't exercised here
    monkeypatch.setenv("SNOWFLAKE_CRAWLER_PASSWORD", "test-pw")

    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("main", None)
    try:
        import main as worker_mod
        importlib.reload(worker_mod)

        # Discovery is dispatched per engine now (metadata.py), so the patch
        # target moved off main. Signature gains `engine` as its first arg.
        monkeypatch.setattr(
            worker_mod.metadata_dispatch, "discover_and_describe",
            lambda engine, conn_params, table_names: dict(_FAKE_METADATA),
        )
        monkeypatch.setattr(worker_mod, "describe_table", lambda table_name, columns, row_count: {
            "keywords": ["OHLCV", "stock price"], "categories": {"ticker": "mã cổ phiếu"},
            "use_cases": ["tính % return"], "join_hints": ["dim_company.ticker"],
        })

        def _fake_embed(text: str):
            h = abs(hash(text))
            return [((h >> (i * 4)) % 97) / 97.0 for i in range(4)]
        monkeypatch.setattr(worker_mod, "embed", _fake_embed)

        from qdrant_client import QdrantClient
        qdrant = QdrantClient(url=qdrant_container["url"])
        worker_mod.ensure_qdrant_collection(qdrant)

        conn = psycopg2.connect(migrated_db["dsn"])
        conn.autocommit = False

        yield worker_mod, conn, qdrant, migrated_db["dsn"]

        conn.close()
    finally:
        sys.modules.pop("main", None)
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def test_crawl_all_creates_postgres_row_and_qdrant_point(worker):
    worker_mod, conn, qdrant, dsn = worker
    tenant_id, warehouse_id = _seed_tenant_and_warehouse(dsn, ["fact_stock_price"])

    worker_mod.crawl_all(conn, qdrant)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT table_name, schema_hash, qdrant_point_id, text_description FROM "SchemaCatalog"
               WHERE tenant_id=%s AND warehouse_id=%s""",
            (tenant_id, warehouse_id),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    table_name, schema_hash, point_id, text_description = rows[0]
    assert table_name == "fact_stock_price"
    assert "OHLCV" in text_description
    assert "dim_company.ticker" in text_description

    point = qdrant.retrieve(worker_mod.QDRANT_COLLECTION, ids=[point_id])
    assert len(point) == 1
    assert point[0].payload["table_name"] == "fact_stock_price"
    assert point[0].payload["tenant_id"] == tenant_id


def test_crawl_all_skips_unchanged_table_on_second_run(worker, monkeypatch):
    worker_mod, conn, qdrant, dsn = worker
    _seed_tenant_and_warehouse(dsn, ["fact_stock_price"])

    worker_mod.crawl_all(conn, qdrant)

    describe_calls = []
    monkeypatch.setattr(worker_mod, "describe_table", lambda *a, **k: describe_calls.append(1) or {
        "keywords": [], "categories": {}, "use_cases": [], "join_hints": [],
    })

    worker_mod.crawl_all(conn, qdrant)

    assert describe_calls == [], "unchanged schema_hash must skip re-describe (diff detection)"


def test_crawl_all_isolates_tenants(worker):
    worker_mod, conn, qdrant, dsn = worker
    tenant_a, warehouse_a = _seed_tenant_and_warehouse(dsn, ["fact_stock_price"])
    tenant_b, warehouse_b = _seed_tenant_and_warehouse(dsn, ["fact_stock_price"])

    worker_mod.crawl_all(conn, qdrant)

    with conn.cursor() as cur:
        cur.execute("""SELECT tenant_id, warehouse_id FROM "SchemaCatalog" ORDER BY tenant_id""")
        rows = {(str(r[0]), str(r[1])) for r in cur.fetchall()}

    assert rows == {(tenant_a, warehouse_a), (tenant_b, warehouse_b)}


def test_crawl_all_skips_warehouse_with_no_password_configured(worker, monkeypatch):
    worker_mod, conn, qdrant, dsn = worker
    _seed_tenant_and_warehouse(dsn, ["fact_stock_price"])
    monkeypatch.delenv("SNOWFLAKE_CRAWLER_PASSWORD", raising=False)
    # connection_params() must return None and crawl_all must not blow up.

    worker_mod.crawl_all(conn, qdrant)

    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "SchemaCatalog"')
        count = cur.fetchone()[0]
    assert count == 0


def test_handle_ddl_event_crawls_single_table(worker, monkeypatch):
    worker_mod, conn, qdrant, dsn = worker
    monkeypatch.setenv("SNOWFLAKE_CRAWLER_PASSWORD", "test-pw")
    tenant_id, warehouse_id = _seed_tenant_and_warehouse(dsn, [])  # no selected_tables — DDL event targets one directly

    worker_mod.handle_ddl_event(conn, qdrant, {
        "tenant_id": tenant_id, "warehouse_id": warehouse_id, "table_name": "fact_stock_price",
    })

    with conn.cursor() as cur:
        cur.execute(
            """SELECT table_name FROM "SchemaCatalog" WHERE tenant_id=%s AND warehouse_id=%s""",
            (tenant_id, warehouse_id),
        )
        rows = cur.fetchall()
    assert rows == [("fact_stock_price",)]
