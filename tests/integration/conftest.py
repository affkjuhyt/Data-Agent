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
import pathlib
import sys
from collections.abc import Iterator
from typing import Callable

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ─── sys.path so `import auth_core` + `from shared.auth import ...` resolve ─
@pytest.fixture(scope="session", autouse=True)
def _auth_api_sys_path() -> Iterator[None]:
    """Mirror the auth_api Dockerfile WORKDIR layout for in-process tests.

    Dockerfile copies `backend/services/auth_api/*` into /app and `backend/services/shared/`
    into /app/shared. So `auth_core` is a top-level module and `shared.auth`
    is a package import. Re-create that layout via sys.path here.
    """
    added = [
        str(REPO_ROOT / "backend" / "services" / "auth_api"),
        str(REPO_ROOT / "backend" / "services"),
    ]
    for p in added:
        if p not in sys.path:
            sys.path.insert(0, p)
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


# ─── Minimal schema — just what the smoke tests exercise ───────────────────
# Source of truth for prod is backend/alembic/versions/b8c9d0e1f2a3_auth_api_secrets_vault_schema.py
# (NOT ff9ae8dcd7f7_initial_schema.py's "SecretsVault"/PascalCase tables —
# those are dead code unrelated to this feature; see b8c9d0e1f2a3's docstring
# for how that drift was found). We intentionally inline a subset here so
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
    openbao_path    text,
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

-- Default governance settings the routes read on startup
INSERT INTO platform_settings (key, value) VALUES
    ('ip_allowlist_mode',    'allow_all'),
    ('vault_panic_mode',     'false'),
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


@pytest.fixture(scope="session")
def auth_api_db(postgres_container) -> dict[str, str]:
    """Apply minimal schema + RLS setup to the session Postgres container.

    Returns the container info dict extended with `app_user_dsn` — the
    DSN clients should use for RLS-enforced queries. The superuser dsn
    (`dsn`) remains in the dict for owner/admin operations (TRUNCATE,
    seed inserts) that should bypass RLS.
    """
    import psycopg2

    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_MINIMAL_SCHEMA)
            cur.execute(_RLS_SETUP)
    finally:
        conn.close()

    host = postgres_container["host"]
    port = postgres_container["port"]
    db = postgres_container["database"]
    return {
        **postgres_container,
        "app_user_dsn": f"postgresql://app_user:{_RLS_PASSWORD}@{host}:{port}/{db}",
    }


# ─── App + client ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def auth_api_app(auth_api_db, _auth_api_sys_path):
    """Construct the FastAPI app with PG_DSN pointing at the test container.

    We patch PG_DSN BEFORE importing app/auth_core so the module-level
    constants pick up the test DSN. Bypasses the startup() task that touches
    Kong + spawns auto-rotation loop — we don't need either for smoke tests.
    """
    os.environ["DATABASE_URL"] = auth_api_db["dsn"]
    # APP_DATABASE_URL is what shared.db_context.get_tenant_db connects to.
    # Must be the app_user role for RLS to apply (owner connections bypass
    # FORCE RLS via the superuser bit testcontainers sets on the owner role).
    os.environ["APP_DATABASE_URL"] = auth_api_db["app_user_dsn"]

    # Late import — must happen after sys.path + env are set
    import auth_core  # type: ignore[import-not-found]
    from app import create_app  # type: ignore[import-not-found]

    # Force the in-memory constant to match the patched env var.
    # auth_core read it at import time, so re-assign now.
    auth_core.PG_DSN = auth_api_db["dsn"]

    app = create_app()
    # Strip the startup hook — it schedules a Kong sync that talks to a
    # nonexistent admin API in tests. Smoke tests cover the routes, not boot.
    app.router.on_startup.clear()
    return app


@pytest.fixture
def client(auth_api_app) -> Iterator:
    """Per-test TestClient. Cleans up auth_api state between tests."""
    from fastapi.testclient import TestClient

    # Reset rows that smoke tests insert. Cheaper than dropping/recreating
    # the schema and lets each test reason about an empty state.
    import psycopg2

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE api_keys, secrets_vault, ip_allowlists, key_rotations RESTART IDENTITY")
    finally:
        conn.close()

    with TestClient(auth_api_app) as c:
        yield c


# ─── Header helpers ────────────────────────────────────────────────────────
TENANT_1 = "11111111-1111-1111-1111-111111111111"
TENANT_2 = "22222222-2222-2222-2222-222222222222"


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
