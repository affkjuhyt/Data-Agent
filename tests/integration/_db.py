"""Database helpers shared by the integration suites.

Every suite shares one session-scoped Postgres container (tests/conftest.py),
and most tests start from an empty database and migrate it. Each file used to
carry its own copy of the reset, and the copies drifted: the alembic chain
leaves `app_user` owning default privileges in the `kb_structured` schema, so
a reset that only dropped `public` failed "role app_user cannot be dropped"
for every test after the first migration.
"""
from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass

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


# ── Migrated database, without re-running the chain for every test ──────────────
# Most tests want "an empty database at alembic head". Resetting and running the
# whole chain took ~3 s per test (~50 tests). The chain is now run once per
# container; afterwards each test only truncates every table and reloads the rows
# the migrations themselves inserted. A fingerprint of the schema guards the shortcut:
# if anything since changed the schema (a downgrade test, a fixture with its own
# DDL, a reset), the next call falls back to reset + full migration.

# Tables, columns, indexes, constraints, policies, triggers, functions, grants and roles:
# what a test can change outside the rows that the restore puts back.
_FINGERPRINT_SQL = """
SELECT md5(coalesce(string_agg(x, '|' ORDER BY x), ''))
FROM (
    SELECT 'c:' || table_schema || '.' || table_name || '.' || column_name || ':' || data_type
           || ':' || is_nullable || ':' || coalesce(column_default, '') AS x
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    UNION ALL
    SELECT 'i:' || schemaname || ':' || indexdef FROM pg_indexes
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    UNION ALL
    SELECT 'k:' || conrelid::regclass::text || ':' || conname || ':' || pg_get_constraintdef(oid)
    FROM pg_constraint WHERE connamespace::regnamespace::text NOT IN ('pg_catalog', 'information_schema')
    UNION ALL
    SELECT 'p:' || schemaname || '.' || tablename || ':' || policyname || ':' || coalesce(qual, '')
    FROM pg_policies
    UNION ALL
    SELECT 't:' || tgrelid::regclass::text || ':' || tgname FROM pg_trigger WHERE NOT tgisinternal
    UNION ALL
    SELECT 'f:' || p.oid::regprocedure::text || ':' || md5(p.prosrc) FROM pg_proc p
    WHERE p.pronamespace::regnamespace::text NOT IN ('pg_catalog', 'information_schema')
    UNION ALL
    SELECT 'g:' || grantee || ':' || table_schema || '.' || table_name || ':' || privilege_type
    FROM information_schema.table_privileges
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    UNION ALL
    SELECT 'r:' || rolname || ':' || rolbypassrls || ':' || rolcanlogin FROM pg_roles
    WHERE rolname NOT LIKE 'pg\\_%'
) s
"""

# Plain and partitioned tables in the application schemas (partitions are plain
# tables in their own right; their parents hold no rows).
_TABLES_SQL = """
SELECT format('%I.%I', n.nspname, c.relname), c.relkind
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
"""

_SEQUENCES_SQL = """
SELECT format('%I.%I', schemaname, sequencename), last_value FROM pg_sequences
WHERE schemaname NOT LIKE 'pg\\_%' AND schemaname <> 'information_schema'
"""


@dataclass
class _Snapshot:
    fingerprint: str
    rows: dict[str, bytes]
    sequences: dict[str, int | None]


_snapshots: dict[str, _Snapshot] = {}


def _fingerprint(cur) -> str:
    cur.execute(_FINGERPRINT_SQL)
    return cur.fetchone()[0]


def _capture(cur) -> _Snapshot:
    cur.execute(_TABLES_SQL)
    rows: dict[str, bytes] = {}
    for table, kind in cur.fetchall():
        if kind != "r":
            continue
        buf = io.BytesIO()
        cur.copy_expert(f"COPY {table} TO STDOUT", buf)
        if buf.tell():
            rows[table] = buf.getvalue()
    cur.execute(_SEQUENCES_SQL)
    sequences = dict(cur.fetchall())
    return _Snapshot(_fingerprint(cur), rows, sequences)


def _restore(cur, snapshot: _Snapshot) -> None:
    cur.execute(_TABLES_SQL)
    tables = [table for table, _kind in cur.fetchall()]
    # replica: no user triggers (append-only guards, audit stamps) and no FK
    # triggers while the snapshot goes back in. A lock held by a connection the
    # previous test leaked fails fast instead of hanging the suite.
    cur.execute("SET lock_timeout = '10s'")
    cur.execute("SET session_replication_role = replica")
    if tables:
        cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    for table, data in snapshot.rows.items():
        cur.copy_expert(f"COPY {table} FROM STDIN", io.BytesIO(data))
    for sequence, last_value in snapshot.sequences.items():
        if last_value is None:
            cur.execute("SELECT setval(%s, 1, false)", (sequence,))
        else:
            cur.execute("SELECT setval(%s, %s, true)", (sequence, last_value))
    cur.execute("SET session_replication_role = DEFAULT")


def migrated_database(dsn: str) -> None:
    """Leave `dsn` at alembic head holding only the rows the migrations insert.

    The first call per container (or after anything changed the schema) resets and
    runs the whole chain; every later call truncates and reloads the snapshot.
    """
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            snapshot = _snapshots.get(dsn)
            if snapshot is not None and _fingerprint(cur) == snapshot.fingerprint:
                _restore(cur, snapshot)
                return
    finally:
        conn.close()

    reset_database(dsn)
    result = run_alembic(dsn, "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            _snapshots[dsn] = _capture(cur)
    finally:
        conn.close()
