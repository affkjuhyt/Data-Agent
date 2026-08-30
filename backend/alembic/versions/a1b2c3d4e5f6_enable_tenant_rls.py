"""enable tenant row-level security

Revision ID: a1b2c3d4e5f6
Revises: ff9ae8dcd7f7
Create Date: 2026-06-07

Adds Postgres Row-Level Security to every public table with a `tenant_id`
column. After this migration:

  * A role `app_user` exists with CRUD privileges on tenant-scoped tables.
  * Every tenant-scoped table has RLS enabled + FORCED.
  * A policy `tenant_isolation` filters rows to those matching the runtime
    setting `app.tenant_id`. Connections that have not set it see no rows.
  * Owner role and superuser are subject to the same policy (FORCE),
    EXCEPT roles explicitly granted `BYPASSRLS`.

⚠️  BREAKING for services that currently connect as the table owner and do
    NOT set `app.tenant_id` before querying. Affected services:
      - workflow_runtime
      - flow_buider
      - kb-backend
      - release_worker
      - audit-bridge / audit-consumer
    Each must adopt `services.shared.db_context.tenant_scoped_db()` or set
    `SET LOCAL app.tenant_id = ...` per request before its routes return.

Emergency rollback: `alembic downgrade -1`. Or grant BYPASSRLS to the owner
role: `ALTER ROLE <owner> BYPASSRLS;` (then revoke when migration done).

Test verification: tests/integration/test_rls_enforcement.py
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "ff9ae8dcd7f7"
branch_labels = None
depends_on = None


_CREATE_APP_USER = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        -- Password set out-of-band (vault, k8s secret, .env). Migration
        -- intentionally does not embed credentials.
        CREATE ROLE app_user LOGIN NOBYPASSRLS;
    ELSE
        ALTER ROLE app_user NOBYPASSRLS;
    END IF;
END
$$;
"""

# Discover every public table that has a tenant_id column, then for each:
#   - GRANT CRUD to app_user
#   - ENABLE + FORCE row-level security
#   - install tenant_isolation policy
#
# Policy logic:
#   USING — read filter. Row is visible iff its tenant_id matches the
#           runtime setting `app.tenant_id`. NULLIF('', '...') handles the
#           unset case so we get NULL (no rows) instead of an error from
#           casting '' to uuid.
#   WITH CHECK — write filter. INSERT/UPDATE must also match. Prevents a
#           caller from smuggling a different tenant_id into a row.
#
# `current_setting('app.tenant_id', true)` — the second arg `true` means
# missing_ok: returns '' if unset rather than raising. We translate to NULL
# via NULLIF so the comparison fails cleanly (no rows).
_APPLY_RLS = r"""
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN
        SELECT table_name
        FROM information_schema.columns
        WHERE column_name  = 'tenant_id'
          AND table_schema = 'public'
        ORDER BY table_name
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', tbl);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        -- Idempotent: a partial/earlier run (or legacy snake_case tables) may
        -- already carry this policy; CREATE POLICY has no IF NOT EXISTS.
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

# asyncpg (the driver this migration runs through) rejects multiple SQL
# statements in one prepared-statement call. Pass each grant individually.
# Single-statement DO blocks are fine because the block IS one statement.
_GRANT_USAGE_SCHEMA   = "GRANT USAGE ON SCHEMA public TO app_user"
_GRANT_SELECT_SEQ     = "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user"
_GRANT_DEFAULT_SEQ    = (
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT USAGE, SELECT ON SEQUENCES TO app_user"
)

_REVERT_RLS = r"""
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN
        SELECT table_name
        FROM information_schema.columns
        WHERE column_name  = 'tenant_id'
          AND table_schema = 'public'
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('REVOKE ALL ON %I FROM app_user', tbl);
    END LOOP;
END
$$;
"""

# asyncpg constraint — same as GRANT block above.
_REVOKE_USAGE_SCHEMA   = "REVOKE USAGE ON SCHEMA public FROM app_user"
_REVOKE_SELECT_SEQ     = "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user"
_REVOKE_DEFAULT_SEQ    = (
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "REVOKE USAGE, SELECT ON SEQUENCES FROM app_user"
)


def upgrade() -> None:
    op.execute(_CREATE_APP_USER)
    op.execute(_GRANT_USAGE_SCHEMA)
    op.execute(_GRANT_SELECT_SEQ)
    op.execute(_GRANT_DEFAULT_SEQ)
    op.execute(_APPLY_RLS)


def downgrade() -> None:
    op.execute(_REVERT_RLS)
    op.execute(_REVOKE_USAGE_SCHEMA)
    op.execute(_REVOKE_SELECT_SEQ)
    op.execute(_REVOKE_DEFAULT_SEQ)
    # Do NOT drop the role — other systems (k8s secrets, vault policies)
    # may still reference it. Drop explicitly via SQL when ops sign off.
