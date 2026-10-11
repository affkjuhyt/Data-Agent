"""Audit in its own database, dataagent_audit (tab 3 D1 item 1, ADR-01).

The design keeps the append-only audit log apart from the business data so it has its own
retention, backup and access. The first baseline applied dataagent_audit.sql into `dataagent`
itself; this revision creates `<database>_audit`, applies the generated audit DDL there and drops
the misplaced `audit` schema here. It refuses to drop that schema while it holds rows, so no audit
record is ever lost by a migration.

Revision ID: v3_0002
Revises: v3_0001
"""

from pathlib import Path

import psycopg
from alembic import op
from psycopg import ClientCursor, sql

revision = "v3_0002"
down_revision = "v3_0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "dataagent_audit.sql"


def upgrade() -> None:
    bind = op.get_bind()
    url = bind.engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    audit_db = f"{url.database}_audit"

    # CREATE DATABASE cannot run inside a transaction: its own autocommit connection.
    with psycopg.connect(**server, dbname=url.database, autocommit=True) as admin:
        if (
            admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (audit_db,)).fetchone()
            is None
        ):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(audit_db)))

    with psycopg.connect(**server, dbname=audit_db) as audit:
        if audit.execute("SELECT to_regnamespace('audit') IS NULL").fetchone()[0]:
            # The script holds PL/pgSQL format() directives (%I, %L): sent as literal SQL.
            with ClientCursor(audit) as cursor:
                cursor.execute(SQL.read_text(encoding="utf-8"))

    misplaced = bind.exec_driver_sql("SELECT to_regnamespace('audit') IS NOT NULL").scalar()
    if misplaced:
        rows = bind.exec_driver_sql(
            "SELECT (SELECT count(*) FROM audit.audit_log) + (SELECT count(*) FROM audit.security_event)"
        ).scalar()
        if rows:
            raise RuntimeError(
                f"audit schema in {url.database} holds {rows} rows; copy them to {audit_db} "
                "(keeping seq, prev_hash and hash) before rerunning"
            )
        bind.exec_driver_sql("DROP SCHEMA audit CASCADE")


def downgrade() -> None:
    raise NotImplementedError("Audit is append-only; restore from a backup instead")
