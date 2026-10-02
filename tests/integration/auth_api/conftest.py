"""auth_api integration fixtures.

Spins a postgres testcontainer once per session, applies a minimal schema
(just the tables the routes touch — NOT full alembic), and exposes the
FastAPI app + TestClient with PG_DSN patched to the container.

OpenBao is NOT mocked at fixture scope — individual tests that hit vault
should patch `auth_core.get_vault` themselves. Smoke tests in this file
hit only routes whose validation rejects before touching vault, so most
tests don't need the patch.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Callable

import pytest

from tests.integration._db import reset_database


# auth_api is imported as the image runs it — `services.auth_api.*` with
# backend/ on the path (pyproject's pythonpath). This file used to put
# backend/services/auth_api first on sys.path for the whole session, which made
# auth_api's own `services/` package shadow the top-level `services` for every
# other integration test collected after it.


# ─── Minimal schema — just what the smoke tests exercise ───────────────────
# Source of truth for prod is backend/alembic/versions/b8c9d0e1f2a3_auth_api_secrets_vault_schema.py
# (NOT ff9ae8dcd7f7_initial_schema.py's "SecretsVault"/PascalCase tables —
# never used, dropped by 2e2377872373; see b8c9d0e1f2a3's docstring for how
# that drift was found). We intentionally inline a subset here so
# tests don't depend on the full migration chain (slow + drift risk). When
# the schema for these tables changes, update this fixture in lockstep.
_MINIMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL,
    created_by    varchar(255) NOT NULL,
    name          varchar(255) NOT NULL,
    key_hash      text NOT NULL,
    key_prefix    varchar(64) NOT NULL,
    scope         varchar(32) NOT NULL,
    last_used_at  timestamptz,
    expires_at    timestamptz,
    revoked_at    timestamptz,
    rotated_from  uuid,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_keys_tenant ON api_keys (tenant_id);

CREATE TABLE IF NOT EXISTS secrets_vault (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    key_name        varchar(255) NOT NULL,
    key_type        varchar(64) NOT NULL,
    algorithm       varchar(64),
    realm           varchar(64),
    version         int NOT NULL DEFAULT 1,
    is_active       boolean NOT NULL DEFAULT true,
    rotation_due_at timestamptz,
    last_rotated_at timestamptz,
    created_by      varchar(255),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_secrets_vault_tenant ON secrets_vault (tenant_id);

CREATE TABLE IF NOT EXISTS ip_allowlists (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL,
    cidr        varchar(64) NOT NULL,
    label       varchar(255),
    is_active   boolean NOT NULL DEFAULT true,
    created_by  varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ip_allowlists_tenant ON ip_allowlists (tenant_id);

CREATE TABLE IF NOT EXISTS platform_settings (
    key        varchar(100) PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS key_rotations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    secret_id     uuid NOT NULL,
    triggered_by  varchar(32) NOT NULL
        CONSTRAINT key_rotations_triggered_by_check
        CHECK (triggered_by IN ('SCHEDULED','MANUAL','PANIC','REVEAL')),
    actor_id      varchar(255),
    old_version   int,
    new_version   int,
    status        varchar(32),
    error         text,
    access_reason text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Per-tenant security switches; every secret route reads vault_panic_mode.
CREATE TABLE IF NOT EXISTS compliance_settings (
    tenant_id        varchar(64) PRIMARY KEY,
    vault_panic_mode boolean NOT NULL DEFAULT false
);

-- Default governance settings the routes read on startup
INSERT INTO platform_settings (key, value) VALUES
    ('ip_allowlist_mode',    'allow_all'),
    ('vault_auto_rotation',  'false'),
    ('vault_pii_access_log', 'false')
ON CONFLICT (key) DO NOTHING;
"""

# Mirror of backend/alembic/versions/a1b2c3d4e5f6_enable_tenant_rls.py for the test
# container. Test DB doesn't run the full migration chain (slow + drift), so
# we hand-roll the same setup here. When the migration changes, update this
# fixture in lockstep.
_RLS_PASSWORD = "test-app-user-pw"
_RLS_SETUP = f"""
-- Idempotent role create
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD '{_RLS_PASSWORD}' NOBYPASSRLS;
    ELSE
        ALTER ROLE app_user LOGIN PASSWORD '{_RLS_PASSWORD}' NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user;

-- Apply RLS to every tenant-scoped table. Mirrors the dynamic-discovery
-- block in the alembic migration but operates on the four tables our test
-- container actually has.
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN
        SELECT table_name
        FROM information_schema.columns
        WHERE column_name = 'tenant_id'
          AND data_type = 'uuid'
          AND table_schema = 'public'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', tbl);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        -- Idempotent: this loop discovers ANY tenant_id-having table in the
        -- shared session container, which can include one an earlier test
        -- file's alembic migration already left behind (with its own
        -- tenant_isolation policy already applied) — e.g. KBWarehouse since
        -- f6a7b8c9d0e1.
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format($p$
            CREATE POLICY tenant_isolation ON %I
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        $p$, tbl);
    END LOOP;
END
$$;
"""


def _app_user_dsn(postgres_container: dict[str, str]) -> str:
    host, port, db = postgres_container["host"], postgres_container["port"], postgres_container["database"]
    return f"postgresql://app_user:{_RLS_PASSWORD}@{host}:{port}/{db}"


@pytest.fixture
def auth_api_db(postgres_container) -> dict[str, str]:
    """Reset the shared container, then apply the minimal schema + RLS setup.

    Per test, not per session: other fixtures reset the same container and
    re-run migrations that recreate `app_user` without this password, so a
    session-scoped setup left later tests with no tables or a failed login.

    Returns the container info dict extended with `app_user_dsn` — the
    DSN clients should use for RLS-enforced queries. The superuser dsn
    (`dsn`) remains in the dict for owner/admin operations (TRUNCATE,
    seed inserts) that should bypass RLS.
    """
    import psycopg2

    reset_database(postgres_container["dsn"])
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_MINIMAL_SCHEMA)
            cur.execute(_RLS_SETUP)
    finally:
        conn.close()

    return {**postgres_container, "app_user_dsn": _app_user_dsn(postgres_container)}


# ─── App + client ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def auth_api_app(postgres_container):
    """Construct the FastAPI app with its DSNs pointing at the test container.

    The env is set BEFORE importing the app so the module-level constants
    (infrastructure/config.py reads DATABASE_URL at import) pick up the test
    DSN. Bypasses the startup() task that touches Kong + spawns the
    auto-rotation loop — we don't need either for smoke tests.
    """
    os.environ["DATABASE_URL"] = postgres_container["dsn"]
    # APP_DATABASE_URL is what shared.db_context.get_tenant_session connects to.
    # Must be the app_user role for RLS to apply (owner connections bypass
    # FORCE RLS via the superuser bit testcontainers sets on the owner role).
    os.environ["APP_DATABASE_URL"] = _app_user_dsn(postgres_container)

    from services.auth_api.app import create_app

    app = create_app()
    # Strip the startup hook — it schedules a Kong sync that talks to a
    # nonexistent admin API in tests. Smoke tests cover the routes, not boot.
    app.router.on_startup.clear()
    return app


@pytest.fixture
def client(auth_api_app, auth_api_db) -> Iterator:
    """Per-test TestClient over a freshly reset schema (see auth_api_db)."""
    from fastapi.testclient import TestClient

    with TestClient(auth_api_app) as c:
        yield c


# ─── Header helpers ────────────────────────────────────────────────────────
TENANT_1 = "11111111-1111-1111-1111-111111111111"
TENANT_2 = "22222222-2222-2222-2222-222222222222"


# ─── Migrated schema — the router tests ────────────────────────────────────
# The router tests below this line assert on what the ORM statements do to real
# rows (tenant scoping, upserts, FOR UPDATE locks), so they run against the
# schema the alembic chain builds — a mapped column the migrations never create
# fails here, not on the cluster. _MINIMAL_SCHEMA above stays for the smoke and
# RLS tests, which want app_user with a known password.
_ROUTER_TABLES = (
    "key_rotations",
    "secrets_vault",
    "api_keys",
    "ip_allowlists",
    "compliance_settings",
    "microsoft_sso_approvals",
)


def _at_alembic_head(dsn: str) -> bool:
    import psycopg2

    from tests.integration._db import alembic_head

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            if not cur.fetchone()[0]:
                return False
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            return row is not None and row[0] == alembic_head()
    finally:
        conn.close()


@pytest.fixture
def migrated_auth_db(postgres_container) -> dict[str, str]:
    """The container at alembic head, auth tables empty, TENANT_1/TENANT_2 seeded.

    The chain takes a while, so it runs only when the database is not already at
    head; otherwise the tables these tests write are emptied. The check is per
    test because auth_api_db (and other suites' fixtures) reset the same
    container in between.
    """
    import psycopg2

    from tests.integration._db import reset_database, run_alembic

    dsn = postgres_container["dsn"]
    if not _at_alembic_head(dsn):
        reset_database(dsn)
        result = run_alembic(dsn, "upgrade", "head")
        assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(_ROUTER_TABLES)}")
            # Back to the defaults b8c9d0e1f2a3 seeds; tests flip these.
            cur.execute("DELETE FROM platform_settings")
            cur.execute(
                "INSERT INTO platform_settings (key, value) VALUES "
                "('ip_allowlist_mode', 'allow_all'), "
                "('vault_auto_rotation', 'false'), ('vault_pii_access_log', 'false')"
            )
            # api_keys / secrets_vault / ip_allowlists reference "Tenants".
            cur.execute(
                'INSERT INTO "Tenants" (id, name, slug) VALUES '
                "(%s, 'Tenant One', 'it-tenant-1'), "
                "(%s, 'Tenant Two', 'it-tenant-2') "
                "ON CONFLICT (id) DO NOTHING",
                (TENANT_1, TENANT_2),
            )
    finally:
        conn.close()
    return postgres_container


@pytest.fixture
def migrated_client(auth_api_app, migrated_auth_db) -> Iterator:
    """TestClient over the migrated schema; server errors come back as 500s."""
    from fastapi.testclient import TestClient

    with TestClient(auth_api_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def db_conn(migrated_auth_db) -> Iterator:
    """An autocommit superuser connection for seeding and reading rows back."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(migrated_auth_db["dsn"], cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def headers_factory() -> Callable[..., dict[str, str]]:
    """Produces the headers Kong injects after JWT verification.

    Default: tenant-1, platform-admin, kong-verified. Override per test by
    passing kwargs.
    """
    def _make(
        tenant: str = TENANT_1,
        user_id: str = "test-user-1",
        roles: str = "platform-admin",
        kong_verified: str = "true",
    ) -> dict[str, str]:
        h = {
            "X-Tenant-Id": tenant,
            "X-User-Id": user_id,
            "X-User-Roles": roles,
        }
        # Allow tests to drop Kong-Verified by passing kong_verified=""
        if kong_verified:
            h["X-Kong-Verified"] = kong_verified
        return h
    return _make
