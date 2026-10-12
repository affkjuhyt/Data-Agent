"""Durable human-approval records and the LangGraph checkpoint schema of the legacy runtime.

Transitional (design tab 6 R-28; docs/v3/03 R0-3): workflow_runtime keeps running beside the V3
blocks until UAT, and its single-agent approvals must survive a pod restart. The target is ADR-37's
own runtime in worker-agent, where a run is agent.agent_run and an approval inbox.hitl_request;
when it replaces workflow_runtime (docs/v3/09 G1) a revision drops the `runtime` and `langgraph`
schemas. The legacy roles exist only on clusters initialised before V3, so they are created
NOLOGIN here when missing; a fresh install does not run workflow_runtime.
"""

from alembic import op

revision = "v3_0003"
down_revision = "v3_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runtime_writer') THEN
            CREATE ROLE runtime_writer NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
            CREATE ROLE app_user NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$;

        CREATE SCHEMA IF NOT EXISTS runtime AUTHORIZATION runtime_writer;
        CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION app_user;

        CREATE TABLE IF NOT EXISTS runtime.pending_review (
            conversation_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            agent_id uuid NOT NULL,
            query text NOT NULL,
            payload jsonb,
            suspended_at timestamptz NOT NULL DEFAULT now(),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0)
        );

        CREATE INDEX IF NOT EXISTS pending_review_tenant_suspended_idx
            ON runtime.pending_review (tenant_id, suspended_at);

        ALTER TABLE runtime.pending_review ENABLE ROW LEVEL SECURITY;
        ALTER TABLE runtime.pending_review FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS pending_review_tenant_isolation ON runtime.pending_review;
        CREATE POLICY pending_review_tenant_isolation ON runtime.pending_review
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

        GRANT USAGE ON SCHEMA runtime TO runtime_writer;
        GRANT USAGE, CREATE ON SCHEMA langgraph TO runtime_writer;
        GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.pending_review TO runtime_writer;
        ALTER TABLE runtime.pending_review OWNER TO runtime_writer;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The V3 runtime review migration is not reversible")
