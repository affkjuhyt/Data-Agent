"""Database helpers shared by the integration suites.

Every suite shares one session-scoped Postgres container (tests/conftest.py),
and most tests start from an empty database and migrate it. Each file used to
carry its own copy of the reset, and the copies drifted: the alembic chain
leaves `app_user` owning default privileges in the `kb_structured` schema, so
a reset that only dropped `public` failed "role app_user cannot be dropped"
for every test after the first migration.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import psycopg2

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


def reset_database(dsn: str) -> None:
    """Return the database to what a fresh container holds.

    Drops every non-system schema and every role other than the connecting
    superuser; `DROP OWNED BY` first, because a role that owns a default
    privilege or a grant anywhere cannot be dropped.
    """
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT nspname FROM pg_namespace
                   WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'"""
            )
            for (schema,) in cur.fetchall():
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute(
                """SELECT rolname FROM pg_roles
                   WHERE rolname NOT LIKE 'pg\\_%' AND rolname <> current_user"""
            )
            for (role,) in cur.fetchall():
                cur.execute(f'DROP OWNED BY "{role}"')
                cur.execute(f'DROP ROLE "{role}"')
    finally:
        conn.close()


def _async_dsn(libpq_dsn: str) -> str:
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the alembic CLI the way the migrate Job does (asyncpg, env.py)."""
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120,
    )


def alembic_head() -> str:
    """The head revision of the chain in the tree, read the way alembic does."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    assert len(heads) == 1, f"the migration chain has {len(heads)} heads: {heads}"
    return heads[0]
