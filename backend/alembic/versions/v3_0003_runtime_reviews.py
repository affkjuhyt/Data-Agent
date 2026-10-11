"""Add durable human-approval records and the LangGraph checkpoint schema."""

from alembic import op

revision = "v3_0003"
down_revision = "v3_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
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
