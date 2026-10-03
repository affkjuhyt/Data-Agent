"""V2 schema and service-role foundation (NOP-147) against a real migrated Postgres.

What has to hold: the five V2 schemas and eight service roles exist after upgrade;
no service role can bypass row-level security or act as superuser; a role writes
only its own schema and sees nothing in another schema until a table migration
grants it; and the migration goes down and back up cleanly.

Privileges are checked with SET ROLE from the superuser connection: after SET ROLE
Postgres applies the target role's privileges, which is what a service login gets.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg2
import psycopg2.errors
import pytest

from tests.integration._db import migrated_database, run_alembic

pytestmark = pytest.mark.integration

REVISION = "5b7d9f1a3c6e"
PREVIOUS = "b1d3f5a7c9e2"
SCHEMAS = {"registry", "ingest", "governance", "knowledge", "alert"}
ROLES = {
    "registry_svc",
    "ingest_svc",
    "crawler_svc",
    "governance_svc",
    "knowledge_svc",
    "alert_svc",
    "outbox_relay",
    "maintenance",
}


def _upgrade(dsn: str, target: str = "head") -> None:
    result = run_alembic(dsn, "upgrade", target)
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def migrated_db(postgres_container: dict[str, str]) -> Iterator[str]:
    migrated_database(postgres_container["dsn"])
    yield postgres_container["dsn"]


def _fetch(dsn: str, sql: str) -> list[tuple[Any, ...]]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows: list[tuple[Any, ...]] = cur.fetchall()
            return rows
    finally:
        conn.close()


def _as_role(dsn: str, role: str, sql: str) -> None:
    """Run one statement with `role`'s privileges; roll back whatever it did."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET ROLE {role}")
            cur.execute(sql)
    finally:
        conn.rollback()
        conn.close()


def test_schemas_roles_and_extension_exist(migrated_db: str) -> None:
    schemas = {r[0] for r in _fetch(migrated_db, "SELECT nspname FROM pg_namespace")}
    assert schemas >= SCHEMAS

    roles = {
        r[0]: r[1:]
        for r in _fetch(
            migrated_db,
            "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles",
        )
    }
    assert roles.keys() >= ROLES
    for role in ROLES:
        superuser, bypassrls, createdb, createrole, login = roles[role]
        assert not superuser and not bypassrls and not createdb and not createrole, role
        assert login, role

    assert _fetch(migrated_db, "SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")


def test_owner_writes_own_schema_and_others_are_denied(migrated_db: str) -> None:
    # Stands in for a later V2 table migration, which runs as the migrating role.
    conn = psycopg2.connect(migrated_db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE registry.probe (id int PRIMARY KEY)")
        cur.execute("CREATE TABLE ingest.probe (id int PRIMARY KEY)")
    conn.close()

    _as_role(migrated_db, "registry_svc", "INSERT INTO registry.probe VALUES (1)")
    _as_role(migrated_db, "ingest_svc", "INSERT INTO ingest.probe VALUES (1)")

    # USAGE on a schema grants nothing on its tables: reads come per table later.
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_role(migrated_db, "ingest_svc", "SELECT * FROM registry.probe")
    # The crawler writes nothing by default, not even in the schema it reads.
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_role(migrated_db, "crawler_svc", "INSERT INTO ingest.probe VALUES (2)")
    # No role reaches a schema it was not given.
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_role(migrated_db, "crawler_svc", "SELECT * FROM registry.probe")
    # Schema layout is owned by migrations, not by services.
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_role(migrated_db, "registry_svc", "CREATE TABLE registry.rogue (id int)")


def test_downgrade_removes_everything_then_reupgrade_is_clean(migrated_db: str) -> None:
    down = run_alembic(migrated_db, "downgrade", PREVIOUS)
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"

    schemas = {r[0] for r in _fetch(migrated_db, "SELECT nspname FROM pg_namespace")}
    assert not SCHEMAS & schemas
    roles = {r[0] for r in _fetch(migrated_db, "SELECT rolname FROM pg_roles")}
    assert not ROLES & roles

    _upgrade(migrated_db, REVISION)
    assert {r[0] for r in _fetch(migrated_db, "SELECT nspname FROM pg_namespace")} >= SCHEMAS


def test_downgrade_refuses_while_a_v2_table_remains(migrated_db: str) -> None:
    conn = psycopg2.connect(migrated_db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE ingest.leftover (id int)")
    conn.close()

    down = run_alembic(migrated_db, "downgrade", PREVIOUS)
    assert down.returncode != 0
    # Nothing was half-removed: the migration runs in one transaction.
    assert {r[0] for r in _fetch(migrated_db, "SELECT rolname FROM pg_roles")} >= ROLES
