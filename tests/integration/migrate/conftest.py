"""The V3 database (backend/alembic/sql, generated from the design's tab 3/3b) on PostgreSQL 16.

`ddl_server` applies dataagent.sql and dataagent_audit.sql once per session to two fresh
databases, `dataagent` and `dataagent_audit`. Each test gets `db` (or `audit_db`): an admin
connection inside a transaction that is rolled back, so tests never see each other's rows.
`as_role` switches that transaction to a service role with the session settings the
application sets (app.org_id, app.user_id, …), which is how row-level security and the
column grants are exercised.

Point DATAAGENT_TEST_ADMIN_DSN at an existing server (any database, superuser) to skip
testcontainers while iterating; otherwise the root `postgres_container` fixture is used.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extensions
import psycopg2.extras
import pytest

# uuid columns come back as uuid.UUID and uuid.UUID parameters are accepted, in every test file
# alike (registering it in one file changed the others' results).
psycopg2.extras.register_uuid()

SQL_DIR = Path(
    os.environ.get("DATAAGENT_TEST_SQL_DIR")
    or Path(__file__).resolve().parents[3] / "backend" / "alembic" / "sql"
)


def _with_db(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _apply(admin_dsn: str, database: str) -> None:
    conn = psycopg2.connect(admin_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{database}"')
        cur.execute(f'CREATE DATABASE "{database}"')
    conn.close()
    target = psycopg2.connect(_with_db(admin_dsn, database))
    with target, target.cursor() as cur:
        cur.execute((SQL_DIR / f"{database}.sql").read_text())
    target.close()


@pytest.fixture(scope="session")
def ddl_server(request: pytest.FixtureRequest) -> dict[str, str]:
    admin = os.environ.get("DATAAGENT_TEST_ADMIN_DSN")
    if not admin:
        admin = request.getfixturevalue("postgres_container")["dsn"]
    for database in ("dataagent", "dataagent_audit"):
        _apply(admin, database)
    return {
        "dataagent": _with_db(admin, "dataagent"),
        "dataagent_audit": _with_db(admin, "dataagent_audit"),
    }


def _tx(dsn: str) -> Iterator[psycopg2.extensions.connection]:
    conn = psycopg2.connect(dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def db(ddl_server: dict[str, str]) -> Iterator[psycopg2.extensions.connection]:
    """Admin connection to `dataagent`; everything it does is rolled back."""
    yield from _tx(ddl_server["dataagent"])


@pytest.fixture
def audit_db(ddl_server: dict[str, str]) -> Iterator[psycopg2.extensions.connection]:
    yield from _tx(ddl_server["dataagent_audit"])
