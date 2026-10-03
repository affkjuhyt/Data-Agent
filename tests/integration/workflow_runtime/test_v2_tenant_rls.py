"""Tenant row-level security for V2 tables (NOP-148) against a real migrated Postgres.

What has to hold, on a probe table protected by `libs/shared/tenant_rls.py` and
queried as the real service roles of NOP-147:
  * a connection with no tenant set sees and writes nothing;
  * tenant A cannot read, update, insert or move rows of tenant B;
  * DELETE matches no row unless the table opted in, and then only its own;
  * a role from another schema cannot write the table at all;
  * a cross-tenant role (the outbox relay) reads every tenant but creates none;
  * the CI guard finds every V2 table that skipped the helper, and finds none
    after `alembic upgrade head`;
  * no role except a superuser can bypass row-level security.

The probe tables stand in for the table migrations of NOP-90/95/104/…, which run
as the same migrating role, so the schema's default privileges apply as they will
there.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg2
import psycopg2.errors
import pytest

from libs.shared.tenant_rls import disable_tenant_rls, enable_tenant_rls
from tests.integration._db import reset_database, run_alembic
from tests.integration._rls import as_service, rls_violations

pytestmark = pytest.mark.integration

TENANT_A = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_B = "bbbbbbbb-0000-0000-0000-000000000002"

# V2 tables that are deliberately not per tenant. Empty until a story adds one,
# and every entry must say why (ADR-V2-28: shared tables live in `registry`).
SHARED_V2_TABLES: tuple[str, ...] = ()


@pytest.fixture
def migrated_db(postgres_container: dict[str, str]) -> Iterator[str]:
    reset_database(postgres_container["dsn"])
    result = run_alembic(postgres_container["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    yield postgres_container["dsn"]


def _admin(dsn: str, *statements: str) -> None:
    """Run as the migrating superuser, committed."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for sql in statements:
                cur.execute(sql)
    finally:
        conn.close()


def _probe(dsn: str, table: str = "rls_probe", **options: object) -> str:
    """ingest.<table> with one row per tenant, under the helper."""
    name = f"ingest.{table}"
    _admin(
        dsn,
        f"""CREATE TABLE {name} (
                id serial PRIMARY KEY,
                tenant_id uuid NOT NULL,
                note text NOT NULL)""",
        f"INSERT INTO {name} (tenant_id, note) VALUES ('{TENANT_A}', 'a'), ('{TENANT_B}', 'b')",
        *enable_tenant_rls("ingest", table, **options),  # type: ignore[arg-type]
    )
    return name


def test_no_tenant_set_sees_and_writes_nothing(migrated_db: str) -> None:
    table = _probe(migrated_db)
    with as_service(migrated_db, "ingest_svc") as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0
    with (
        pytest.raises(psycopg2.errors.InsufficientPrivilege),
        as_service(migrated_db, "ingest_svc") as cur,
    ):
        cur.execute(f"INSERT INTO {table} (tenant_id, note) VALUES ('{TENANT_A}', 'x')")


def test_a_tenant_reads_only_its_own_rows(migrated_db: str) -> None:
    table = _probe(migrated_db)
    with as_service(migrated_db, "ingest_svc", TENANT_A) as cur:
        cur.execute(f"SELECT tenant_id::text, note FROM {table}")
        assert cur.fetchall() == [(TENANT_A, "a")]


def test_a_tenant_cannot_update_or_move_rows_across_tenants(migrated_db: str) -> None:
    table = _probe(migrated_db)
    with as_service(migrated_db, "ingest_svc", TENANT_A) as cur:
        cur.execute(f"UPDATE {table} SET note = 'hacked' WHERE tenant_id = '{TENANT_B}'")
        assert cur.rowcount == 0
        cur.execute(f"UPDATE {table} SET note = 'mine' WHERE tenant_id = '{TENANT_A}'")
        assert cur.rowcount == 1
    # Handing an own row to another tenant fails the UPDATE's WITH CHECK.
    with (
        pytest.raises(psycopg2.errors.InsufficientPrivilege),
        as_service(migrated_db, "ingest_svc", TENANT_A) as cur,
    ):
        cur.execute(f"UPDATE {table} SET tenant_id = '{TENANT_B}'")


def test_a_tenant_cannot_insert_rows_for_another_tenant(migrated_db: str) -> None:
    table = _probe(migrated_db)
    with as_service(migrated_db, "ingest_svc", TENANT_A) as cur:
        cur.execute(f"INSERT INTO {table} (tenant_id, note) VALUES ('{TENANT_A}', 'ok')")
    with (
        pytest.raises(psycopg2.errors.InsufficientPrivilege),
        as_service(migrated_db, "ingest_svc", TENANT_A) as cur,
    ):
        cur.execute(f"INSERT INTO {table} (tenant_id, note) VALUES ('{TENANT_B}', 'planted')")


def test_delete_matches_nothing_unless_the_table_allows_it(migrated_db: str) -> None:
    append_only = _probe(migrated_db)
    with as_service(migrated_db, "ingest_svc", TENANT_A) as cur:
        cur.execute(f"DELETE FROM {append_only}")
        assert cur.rowcount == 0

    deletable = _probe(migrated_db, "rls_probe_deletable", allow_delete=True)
    with as_service(migrated_db, "ingest_svc", TENANT_A) as cur:
        cur.execute(f"DELETE FROM {deletable}")
        assert cur.rowcount == 1  # its own row only
        cur.execute(f"SELECT count(*) FROM {deletable} WHERE tenant_id = '{TENANT_B}'")
        assert cur.fetchone()[0] == 0  # B's row is still there, just invisible to A


@pytest.mark.parametrize("role", ["registry_svc", "governance_svc", "crawler_svc", "outbox_relay"])
def test_a_role_from_another_schema_cannot_write(migrated_db: str, role: str) -> None:
    table = _probe(migrated_db)
    with (
        pytest.raises(psycopg2.errors.InsufficientPrivilege),
        as_service(migrated_db, role, TENANT_A) as cur,
    ):
        cur.execute(f"INSERT INTO {table} (tenant_id, note) VALUES ('{TENANT_A}', 'x')")


def test_a_cross_tenant_role_reads_every_tenant_but_creates_nothing(migrated_db: str) -> None:
    table = _probe(migrated_db, "rls_probe_outbox", cross_tenant_roles=("outbox_relay",))
    _admin(migrated_db, f"GRANT SELECT, INSERT, UPDATE ON {table} TO outbox_relay")

    with as_service(migrated_db, "outbox_relay") as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 2
        cur.execute(f"UPDATE {table} SET note = 'published'")
        assert cur.rowcount == 2
    # Granted INSERT, yet no policy lets it create a row without a tenant.
    with (
        pytest.raises(psycopg2.errors.InsufficientPrivilege),
        as_service(migrated_db, "outbox_relay") as cur,
    ):
        cur.execute(f"INSERT INTO {table} (tenant_id, note) VALUES ('{TENANT_A}', 'x')")
    # Other roles stay tenant-scoped on the same table.
    with as_service(migrated_db, "ingest_svc", TENANT_B) as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 1


def test_the_helper_is_idempotent_and_reversible(migrated_db: str) -> None:
    table = _probe(migrated_db)
    _admin(migrated_db, *enable_tenant_rls("ingest", "rls_probe"))
    assert rls_violations(migrated_db) == []

    _admin(migrated_db, *disable_tenant_rls("ingest", "rls_probe"))
    assert rls_violations(migrated_db) == [
        "ingest.rls_probe: RLS not enabled, RLS not forced, no SELECT policy"
    ]
    with as_service(migrated_db, "ingest_svc") as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 2


def test_every_v2_table_after_upgrade_head_is_tenant_isolated(migrated_db: str) -> None:
    """The CI guard: a V2 table migration that forgot the helper fails here."""
    assert rls_violations(migrated_db, shared_tables=SHARED_V2_TABLES) == []


def test_the_guard_catches_each_way_a_table_can_skip_the_helper(migrated_db: str) -> None:
    _probe(migrated_db)  # protected: must not be reported
    _admin(
        migrated_db,
        "CREATE TABLE registry.no_rls (id int, tenant_id uuid NOT NULL)",
        "CREATE TABLE governance.not_forced (id int, tenant_id uuid NOT NULL)",
        "ALTER TABLE governance.not_forced ENABLE ROW LEVEL SECURITY",
        "CREATE TABLE knowledge.no_tenant (id int)",
        "CREATE TABLE alert.nullable_tenant (id int, tenant_id uuid)",
        *enable_tenant_rls("alert", "nullable_tenant"),
        "CREATE TABLE registry.unit (code text PRIMARY KEY)",
    )

    assert rls_violations(migrated_db, shared_tables=["registry.unit"]) == [
        "alert.nullable_tenant: tenant_id is nullable",
        "governance.not_forced: RLS not forced, no SELECT policy",
        "knowledge.no_tenant: no tenant_id column, RLS not enabled, RLS not forced, no SELECT policy",
        "registry.no_rls: RLS not enabled, RLS not forced, no SELECT policy",
    ]


def test_no_role_but_a_superuser_bypasses_row_level_security(migrated_db: str) -> None:
    conn = psycopg2.connect(migrated_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolname FROM pg_roles WHERE rolbypassrls AND NOT rolsuper ORDER BY 1"
            )
            assert cur.fetchall() == []
    finally:
        conn.close()
