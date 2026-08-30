"""Row-Level Security enforcement for KB child tables that lack a tenant_id column.

Pins the contract of backend/alembic/versions/f1a2b3c4d5e6_rls_kb_child_tables.py: a child
table (KBTextBlock, keyed to KBData via owner_id) must isolate tenants through the
FK chain, so `app_user` cannot read or write another tenant's rows even when it
queries the child table DIRECTLY (the gap the migration closes). The real policy
predicate is imported from the migration module so this test breaks if the
predicate is weakened.

Self-contained: builds a minimal KBData(parent)+KBTextBlock(child) schema on the
session Postgres container rather than running the full alembic chain.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.integration

psycopg2 = pytest.importorskip("psycopg2")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "f1a2b3c4d5e6_rls_kb_child_tables.py"

TENANT_1 = "11111111-1111-1111-1111-111111111111"
TENANT_2 = "22222222-2222-2222-2222-222222222222"
APP_PW = "app_pw"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS "KBData" (
    data_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    name      text
);
CREATE TABLE IF NOT EXISTS "KBTextBlock" (
    block_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid NOT NULL REFERENCES "KBData"(data_id),
    block_index int
);
"""

_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _load_migration():
    spec = importlib.util.spec_from_file_location("rls_child_mig", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kb_child_rls(postgres_container):
    """Provision schema + app_user + parent/child RLS, seed two tenants. Returns
    {app_dsn, t1_block, t2_block, t1_data, t2_data}."""
    mig = _load_migration()
    child_predicate = mig._CHILD_PREDICATES["KBTextBlock"]
    child_policy = mig._POLICY

    owner = psycopg2.connect(postgres_container["dsn"])
    owner.autocommit = True
    ids: dict[str, str] = {}
    try:
        with owner.cursor() as cur:
            cur.execute(_SCHEMA)
            # app_user — subject to RLS (NOBYPASSRLS), password login.
            cur.execute(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN "
                f"  CREATE ROLE app_user LOGIN PASSWORD '{APP_PW}' NOBYPASSRLS; "
                "ELSE ALTER ROLE app_user NOBYPASSRLS; END IF; END $$;"
            )
            cur.execute("GRANT USAGE ON SCHEMA public TO app_user")
            for tbl in ('"KBData"', '"KBTextBlock"'):
                cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO app_user")

            # Parent policy (mirrors the base RLS migration).
            cur.execute('ALTER TABLE "KBData" ENABLE ROW LEVEL SECURITY')
            cur.execute('ALTER TABLE "KBData" FORCE ROW LEVEL SECURITY')
            cur.execute('DROP POLICY IF EXISTS tenant_isolation ON "KBData"')
            cur.execute(
                'CREATE POLICY tenant_isolation ON "KBData" AS PERMISSIVE FOR ALL TO PUBLIC '
                f"USING (tenant_id = {_TID}) WITH CHECK (tenant_id = {_TID})"
            )
            # Child policy — the REAL predicate under test.
            cur.execute('ALTER TABLE "KBTextBlock" ENABLE ROW LEVEL SECURITY')
            cur.execute('ALTER TABLE "KBTextBlock" FORCE ROW LEVEL SECURITY')
            cur.execute(f'DROP POLICY IF EXISTS {child_policy} ON "KBTextBlock"')
            cur.execute(
                f'CREATE POLICY {child_policy} ON "KBTextBlock" AS PERMISSIVE FOR ALL TO PUBLIC '
                f"USING ({child_predicate}) WITH CHECK ({child_predicate})"
            )

            # Seed one document + one block per tenant (as owner — bypasses RLS).
            for label, tid in (("t1", TENANT_1), ("t2", TENANT_2)):
                cur.execute(
                    'INSERT INTO "KBData" (tenant_id, name) VALUES (%s, %s) RETURNING data_id',
                    (tid, f"doc-{label}"),
                )
                data_id = cur.fetchone()[0]
                cur.execute(
                    'INSERT INTO "KBTextBlock" (owner_id, block_index) VALUES (%s, 0) '
                    "RETURNING block_id",
                    (data_id,),
                )
                block_id = cur.fetchone()[0]
                ids[f"{label}_data"] = str(data_id)
                ids[f"{label}_block"] = str(block_id)
    finally:
        owner.close()

    host = postgres_container["host"]
    port = postgres_container["port"]
    db = postgres_container["database"]
    ids["app_dsn"] = f"postgresql://app_user:{APP_PW}@{host}:{port}/{db}"
    return ids


def _app_conn(dsn: str, tenant_id: str | None):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id or "",))
    return conn


def test_child_table_direct_select_scoped_to_tenant(kb_child_rls):
    """A WHERE-less SELECT on the CHILD table returns only the caller's tenant."""
    conn = _app_conn(kb_child_rls["app_dsn"], TENANT_1)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT block_id FROM "KBTextBlock"')
            rows = {str(r[0]) for r in cur.fetchall()}
        assert rows == {kb_child_rls["t1_block"]}
        assert kb_child_rls["t2_block"] not in rows
    finally:
        conn.close()


def test_child_table_other_tenant_cannot_see_row(kb_child_rls):
    conn = _app_conn(kb_child_rls["app_dsn"], TENANT_2)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT block_id FROM "KBTextBlock" WHERE block_id = %s',
                        (kb_child_rls["t1_block"],))
            assert cur.fetchone() is None
    finally:
        conn.close()


def test_child_table_no_tenant_setting_sees_nothing(kb_child_rls):
    """Unset app.tenant_id → quietly empty, not everything, not an error."""
    conn = _app_conn(kb_child_rls["app_dsn"], None)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM "KBTextBlock"')
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_child_insert_referencing_foreign_parent_is_blocked(kb_child_rls):
    """WITH CHECK: tenant-1 app_user cannot insert a block owned by tenant-2's doc."""
    conn = _app_conn(kb_child_rls["app_dsn"], TENANT_1)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.Error):
                cur.execute(
                    'INSERT INTO "KBTextBlock" (owner_id, block_index) VALUES (%s, 1)',
                    (kb_child_rls["t2_data"],),
                )
        conn.rollback()
    finally:
        conn.close()


def test_child_insert_referencing_own_parent_succeeds(kb_child_rls):
    conn = _app_conn(kb_child_rls["app_dsn"], TENANT_1)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "KBTextBlock" (owner_id, block_index) VALUES (%s, 9) '
                "RETURNING block_id",
                (kb_child_rls["t1_data"],),
            )
            assert cur.fetchone()[0] is not None
        conn.rollback()  # keep seed reproducible for other tests
    finally:
        conn.close()
