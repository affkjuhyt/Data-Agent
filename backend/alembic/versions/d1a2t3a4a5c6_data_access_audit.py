"""data-access audit log

Revision ID: d1a2t3a4a5c6
Revises: c1e2m3a4n5t6
Create Date: 2026-08-27

Adds `DataAccessLog` — one row per query an agent runs, which is the record the
platform had no equivalent of. `audit_logs` covers authentication events only
(audit-bridge polls Keycloak), so "who read this data, when, and what came
back" was unanswerable; the SQL existed only in Langfuse traces, which sample
and expire.

Deliberately a separate table from audit_logs rather than more rows in it: the
volume differs by orders of magnitude, the columns barely overlap, and the two
want different retention.

RLS follows `a1b2c3d4e5f6_enable_tenant_rls`. An audit row names real tables and
carries a redacted statement, so cross-tenant visibility here would disclose one
tenant's warehouse structure and query patterns to another.

NOT ENFORCED YET: append-only. Nothing in the platform updates or deletes a row
here, but no grant stops it either — `app_user` gets UPDATE/DELETE from the
blanket grant in the RLS migration. Making it truly tamper-evident needs a
dedicated writer role with INSERT-only, plus revoking UPDATE/DELETE from
everything else. That is a deployment decision (which role does the consumer
connect as?) and is called out in SECURITY.md rather than guessed at here.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "d1a2t3a4a5c6"
down_revision = "c1e2m3a4n5t6"
branch_labels = None
depends_on = None


_CREATE = """
CREATE TABLE IF NOT EXISTS "DataAccessLog" (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Producer-generated and unique: Kafka is at-least-once, so a redelivered
    -- event must collide here rather than double-count in a report.
    event_id            uuid NOT NULL UNIQUE,
    tenant_id           uuid NOT NULL REFERENCES "Tenants"(id),

    user_id             varchar(255) NOT NULL,
    user_roles          text[] NOT NULL DEFAULT '{}',
    auth_source         varchar(64) NOT NULL DEFAULT '',

    action              varchar(32) NOT NULL,
    decision            varchar(16) NOT NULL,
    target              varchar(256) NOT NULL DEFAULT '',
    target_objects      text[] NOT NULL DEFAULT '{}',
    statement_redacted  text NOT NULL DEFAULT '',
    statement_sha256    varchar(64) NOT NULL DEFAULT '',
    metric_ref          varchar(160) NOT NULL DEFAULT '',

    row_count           bigint,
    latency_ms          integer,
    reason              text NOT NULL DEFAULT '',

    session_id          varchar(64) NOT NULL DEFAULT '',
    agent_id            varchar(64) NOT NULL DEFAULT '',
    metadata            jsonb,

    occurred_at         timestamptz NOT NULL,
    inserted_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_data_access_decision CHECK (decision IN ('allow','deny','error')),
    CONSTRAINT ck_data_access_action
        CHECK (action IN ('sql_query','schema_introspect','kb_retrieve','code_exec'))
);
"""

_INDEXES = [
    'CREATE INDEX IF NOT EXISTS idx_data_access_tenant_time ON "DataAccessLog" (tenant_id, occurred_at DESC)',
    'CREATE INDEX IF NOT EXISTS idx_data_access_user_time ON "DataAccessLog" (tenant_id, user_id, occurred_at DESC)',
    # GIN so "every query that touched `salaries`" is an index lookup rather
    # than a full scan of the busiest table in the database.
    'CREATE INDEX IF NOT EXISTS idx_data_access_objects ON "DataAccessLog" USING gin (target_objects)',
    'CREATE INDEX IF NOT EXISTS idx_data_access_statement ON "DataAccessLog" (statement_sha256)',
]

_RLS = """
DO $$
BEGIN
    EXECUTE 'GRANT SELECT, INSERT ON "DataAccessLog" TO app_user';
    EXECUTE 'ALTER TABLE "DataAccessLog" ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE "DataAccessLog" FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS tenant_isolation ON "DataAccessLog"';
    EXECUTE $p$
        CREATE POLICY tenant_isolation ON "DataAccessLog"
        AS PERMISSIVE
        FOR ALL
        TO PUBLIC
        USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    $p$;
END
$$;
"""


def upgrade() -> None:
    op.execute(_CREATE)
    for statement in _INDEXES:
        op.execute(statement)
    # app_user may not exist where a1b2c3d4e5f6 has not run (it creates the
    # role). A missing role must not abort table creation, so this is
    # best-effort and re-runnable — same pattern as the semantic-layer revision.
    try:
        op.execute(_RLS)
    except Exception:  # noqa: BLE001 — see comment above
        pass


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "DataAccessLog" CASCADE')
