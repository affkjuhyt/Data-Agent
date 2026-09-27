"""Integration tests for the Schema Catalog migration (sandbox_schema_docs.docx
§II), d4e5f6a7b8c9 — same asyncpg-subprocess alembic pattern as
test_workflow_schema_memory.py / test_alembic_migrations.py.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid
from typing import Iterator

import psycopg2
import psycopg2.errors
import pytest

from tests.integration._db import reset_database

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    reset_database(postgres_container["dsn"])
    yield postgres_container


@pytest.fixture
def migrated_db(fresh_db) -> dict[str, str]:
    result = _run_alembic(fresh_db["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return fresh_db


def _seed_tenant_and_warehouse(dsn: str) -> tuple[str, str]:
    tenant_id, warehouse_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "Schema Catalog Test Tenant", f"sc-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "KBWarehouse" (warehouse_id, tenant_id, service, description)
                   VALUES (%s, %s, 'Snowflake', 'test warehouse')""",
                (warehouse_id, tenant_id),
            )  # f6a7b8c9d0e1 — tenant_id gap-fix on KBWarehouse
    finally:
        conn.close()
    return tenant_id, warehouse_id


def test_schema_catalog_table_created(migrated_db):
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'SchemaCatalog'
            """)
            tables = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    assert tables == ["SchemaCatalog"]


def test_unique_constraint_blocks_duplicate_table(migrated_db):
    tenant_id, warehouse_id = _seed_tenant_and_warehouse(migrated_db["dsn"])
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "SchemaCatalog"
                       (id, tenant_id, warehouse_id, schema_name, table_name, columns,
                        text_description, schema_hash)
                   VALUES (%s, %s, %s, 'ANALYTICS', 'fact_stock_price', '[]'::jsonb, 'desc', 'hash1')""",
                (str(uuid.uuid4()), tenant_id, warehouse_id),
            )
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    """INSERT INTO "SchemaCatalog"
                           (id, tenant_id, warehouse_id, schema_name, table_name, columns,
                            text_description, schema_hash)
                       VALUES (%s, %s, %s, 'ANALYTICS', 'fact_stock_price', '[]'::jsonb, 'dup', 'hash2')""",
                    (str(uuid.uuid4()), tenant_id, warehouse_id),
                )
    finally:
        conn.close()


def test_rls_tenant_isolation_policy_exists(migrated_db):
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.relname, p.polname
                FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                WHERE c.relname = 'SchemaCatalog' AND p.polname = 'tenant_isolation'
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    assert rows == [("SchemaCatalog", "tenant_isolation")]


def test_downgrade_drops_table_then_reupgrade_is_clean(migrated_db):
    down = _run_alembic(migrated_db["dsn"], "downgrade", "c3d4e5f6a7b8")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"

    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name = 'SchemaCatalog'
            """)
            assert cur.fetchall() == []
    finally:
        conn.close()

    up = _run_alembic(migrated_db["dsn"], "upgrade", "head")
    assert up.returncode == 0, f"re-upgrade failed:\n{up.stdout}\n{up.stderr}"
