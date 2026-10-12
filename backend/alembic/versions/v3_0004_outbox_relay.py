"""The interim outbox relay role, svc_outbox_relay, on clusters that already ran the baseline.

Debezium reads outbox rows from the WAL, past row-level security (ADR-06); until Kafka Connect
runs, a polling relay publishes them. It sees every organisation's outbox rows, may only set
published_at, and has no grant on any other table. Fresh installs get the same grants from the
generated dataagent.sql (backend/schema/csdl/gen_ddl.py); this revision is idempotent so both
paths end equal.

Revision ID: v3_0004
Revises: v3_0003
"""

from alembic import op

revision = "v3_0004"
down_revision = "v3_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          s text;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_outbox_relay') THEN
            CREATE ROLE svc_outbox_relay NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
          FOR s IN
            SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'outbox' AND c.relkind = 'r'
          LOOP
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO svc_outbox_relay', s);
            EXECUTE format('GRANT SELECT, UPDATE (published_at) ON %I.outbox TO svc_outbox_relay', s);
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = s AND tablename = 'outbox'
                           AND policyname = 'outbox_relay_read') THEN
              EXECUTE format('CREATE POLICY outbox_relay_read ON %I.outbox FOR SELECT '
                             'TO svc_outbox_relay USING (true)', s);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = s AND tablename = 'outbox'
                           AND policyname = 'outbox_relay_publish') THEN
              EXECUTE format('CREATE POLICY outbox_relay_publish ON %I.outbox FOR UPDATE '
                             'TO svc_outbox_relay USING (published_at IS NULL) '
                             'WITH CHECK (published_at IS NOT NULL)', s);
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Remove the relay role once Debezium publishes the outbox (ADR-06)")
