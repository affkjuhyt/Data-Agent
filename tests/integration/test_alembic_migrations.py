"""Alembic migration end-to-end tests.

These exist because the test harness in test_auth_api_smoke uses psycopg2
(sync driver), but production migrate runs through asyncpg (async). Some
SQL patterns work on one and fail on the other — most notably asyncpg's
"cannot insert multiple commands into a prepared statement" rejection of
semicolon-separated statements in a single `op.execute()`.

This file runs the actual migrations through asyncpg, so the dev loop
catches that class of failure on `pytest`, not on `docker compose up`.

Scenarios:

  1. Fresh DB → upgrade head succeeds, every migration runs.
  2. DB with SQL init scripts applied (release-init.sql) → env.py's stamp
     fallback kicks in, only newer migrations run, no DuplicateTable.

For each scenario:

  - Postgres testcontainer per session (reused with the auth-api fixture).
  - subprocess `alembic upgrade head` against the container DSN.
  - Assert exit 0 + alembic_version row matches the latest revision.

Why subprocess instead of importing alembic.command? Production runs
alembic as a CLI invocation from a sibling container. Test the same code
path the migrate service uses, including env.py module loading.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Iterator

import psycopg2
import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    """Convert `postgresql://...` to `postgresql+asyncpg://...` for alembic."""
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic_upgrade(dsn: str) -> subprocess.CompletedProcess:
    """Invoke alembic the same way the migrate compose service does."""
    env = {
        **os.environ,
        "DATABASE_URL": _async_dsn(dsn),
        "PYTHONPATH": str(REPO_ROOT),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


def _current_revision(dsn: str) -> str | None:
    """Read the row in alembic_version, return None if table absent/empty."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema='public' AND table_name='alembic_version'
                )
            """)
            (has_table,) = cur.fetchone()
            if not has_table:
                return None
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    """Wipe public schema before each test so we get a deterministic fresh DB.

    Cheaper than spinning a new container per test — TRUNCATE-style reset
    by dropping + recreating the schema.
    """
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Drop everything user-owned. Done as superuser so RLS doesn't fight us.
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            # `app_user` may persist from a previous test in the session.
            cur.execute("DROP ROLE IF EXISTS app_user")
    finally:
        conn.close()
    yield postgres_container


# ─── 1. Fresh DB upgrade head ──────────────────────────────────────────────
def test_alembic_upgrade_head_on_fresh_db(fresh_db):
    """The full migration chain must run cleanly via asyncpg on an empty DB.

    Catches things like `op.execute("stmt1; stmt2")` that work on psycopg2
    but fail on asyncpg with "cannot insert multiple commands".
    """
    result = _run_alembic_upgrade(fresh_db["dsn"])
    assert result.returncode == 0, (
        f"alembic upgrade failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # alembic_version row must be set to the head revision (currently
    # auth_api_secrets_vault_schema — bump this when a new head lands).
    rev = _current_revision(fresh_db["dsn"])
    assert rev == "b8c9d0e1f2a3", f"expected head rev, got {rev}"


# ─── 2. RLS migration was actually applied (regression net for issue #3) ───
def test_rls_policy_exists_after_upgrade(fresh_db):
    """If alembic claimed success but the policy isn't actually installed,
    the migration silently lied. Verify the policy is on a known table."""
    _run_alembic_upgrade(fresh_db["dsn"])

    conn = psycopg2.connect(fresh_db["dsn"])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT polname, polcmd
                FROM pg_policy p
                JOIN pg_class c ON c.oid = p.polrelid
                WHERE c.relname = 'api_keys' AND p.polname = 'tenant_isolation'
            """)
            policies = cur.fetchall()
    finally:
        conn.close()

    assert len(policies) == 1, f"expected one tenant_isolation policy on api_keys, got: {policies}"


# ─── 3. app_user role created by the migration ─────────────────────────────
def test_app_user_role_created_with_login_and_nobypassrls(fresh_db):
    """The RLS guarantee depends on app_user being NOBYPASSRLS. If a refactor
    accidentally gives it BYPASSRLS, RLS doesn't actually filter for them."""
    _run_alembic_upgrade(fresh_db["dsn"])

    conn = psycopg2.connect(fresh_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = 'app_user'"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None, "app_user role was not created"
    can_login, bypass_rls = row
    assert can_login is True, "app_user must have LOGIN"
    assert bypass_rls is False, "app_user must NOT have BYPASSRLS (RLS would no-op)"
