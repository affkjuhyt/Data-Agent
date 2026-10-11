"""The Alembic chain the migrate Job runs: dataagent, then the audit database apart from it."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ALEMBIC = Path(__file__).resolve().parents[3] / "backend" / "alembic"


def _db(dsn: str, name: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{name}"))


def _fresh(admin: str, *names: str) -> None:
    conn = psycopg2.connect(admin)
    conn.autocommit = True
    with conn.cursor() as cur:
        for name in names:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{names[0]}"')
    conn.close()


def _alembic(dsn: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC / "alembic.ini"), *args],
        env={**os.environ, "DATABASE_URL": dsn},
        check=True,
        cwd=ALEMBIC,
    )


def _one(dsn: str, query: str):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def admin(ddl_server) -> str:
    return _db(ddl_server["dataagent"], "postgres")


def test_a_fresh_install_puts_audit_in_its_own_database(admin):
    _fresh(admin, "chain_fresh", "chain_fresh_audit")
    _alembic(_db(admin, "chain_fresh"), "upgrade", "v3_0002")
    assert _one(_db(admin, "chain_fresh"), "SELECT version_num FROM alembic_version") == "v3_0002"
    assert _one(_db(admin, "chain_fresh"), "SELECT to_regnamespace('audit') IS NULL") is True
    assert (
        _one(_db(admin, "chain_fresh_audit"), "SELECT to_regclass('audit.audit_log') IS NOT NULL")
        is True
    )


def test_a_cluster_with_audit_inside_dataagent_is_moved(admin):
    # The first baseline also applied the audit DDL to dataagent; v3_0002 moves it out.
    _fresh(admin, "chain_old", "chain_old_audit")
    old = _db(admin, "chain_old")
    _alembic(old, "upgrade", "v3_0001")
    conn = psycopg2.connect(old)
    with conn, conn.cursor() as cur:
        cur.execute((ALEMBIC / "sql" / "dataagent_audit.sql").read_text())
    conn.close()
    _alembic(old, "upgrade", "v3_0002")
    assert _one(old, "SELECT to_regnamespace('audit') IS NULL") is True
    assert (
        _one(_db(admin, "chain_old_audit"), "SELECT to_regclass('audit.audit_log') IS NOT NULL")
        is True
    )
