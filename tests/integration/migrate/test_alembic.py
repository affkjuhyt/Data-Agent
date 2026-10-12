"""The Alembic chain the migrate Job runs: dataagent, then the audit database apart from it."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from alembic.script import ScriptDirectory

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


def test_a_fresh_install_runs_the_whole_chain_and_puts_audit_apart(admin):
    # Every revision must apply on a database that never saw the legacy schema or its roles.
    _fresh(admin, "chain_fresh", "chain_fresh_audit")
    _alembic(_db(admin, "chain_fresh"), "upgrade", "head")
    head = ScriptDirectory(str(ALEMBIC)).get_current_head()
    assert _one(_db(admin, "chain_fresh"), "SELECT version_num FROM alembic_version") == head
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


def test_an_existing_audit_database_gets_the_export_job(admin):
    # A cluster upgraded before 12/10/2026 has the audit database without audit.export_job.
    _fresh(admin, "chain_export", "chain_export_audit")
    _alembic(_db(admin, "chain_export"), "upgrade", "v3_0005")
    audit = _db(admin, "chain_export_audit")
    conn = psycopg2.connect(audit)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE audit.export_job")
    conn.close()
    _alembic(_db(admin, "chain_export"), "upgrade", "head")
    assert _one(audit, "SELECT to_regclass('audit.export_job') IS NOT NULL") is True
    assert (
        _one(audit, "SELECT relrowsecurity FROM pg_class WHERE oid = 'audit.export_job'::regclass")
        is True
    )
    assert (
        _one(audit, "SELECT has_table_privilege('svc_audit', 'audit.export_job', 'INSERT')") is True
    )


def test_the_decisions_of_12_10_apply_to_a_migrated_cluster(admin):
    _fresh(admin, "chain_1210", "chain_1210_audit")
    db, audit = _db(admin, "chain_1210"), _db(admin, "chain_1210_audit")
    _alembic(db, "upgrade", "v3_0008")
    # Put back what a cluster migrated before 12/10/2026 has (the baseline is generated today).
    for dsn, sql in (
        (audit, "DROP TABLE audit.outbox"),
        (db, "ALTER TABLE catalog.source DROP COLUMN writeback_tables"),
        (db, "ALTER TABLE pipeline.data_product DROP COLUMN owner_id"),
        (db, "ALTER TABLE ingest.quality_result DROP COLUMN acknowledged_note"),
        (db, "DROP INDEX conflict.source_authority_data_space_id_data_kind_key_2"),
    ):
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
    _alembic(db, "upgrade", "head")
    assert _one(audit, "SELECT to_regclass('audit.outbox') IS NOT NULL") is True
    assert _one(audit, "SELECT has_table_privilege('svc_outbox_relay', 'audit.outbox', 'SELECT')")
    for schema, table, name in (
        ("catalog", "source", "writeback_tables"),
        ("pipeline", "data_product", "owner_id"),
        ("ingest", "quality_result", "acknowledged_note"),
    ):
        assert (
            _one(
                db,
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                f"'{schema}' AND table_name = '{table}' AND column_name = '{name}'",
            )
            == 1
        )
    assert _one(
        db,
        "SELECT to_regclass('conflict.source_authority_data_space_id_data_kind_key_2') IS NOT NULL",
    )
