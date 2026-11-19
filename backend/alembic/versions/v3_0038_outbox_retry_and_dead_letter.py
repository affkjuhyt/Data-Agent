"""Every outbox records a failed try, retries it later, and leaves it dead after five (PL-02).

A handler that raised used to roll its whole batch back: the batch came back on the next claim, the
failing event first, forever, and the events behind it waited (docs/blueprint/ARCHITECTURE.md §7,
the 11 worker-agent tests one stuck question.received took down). Each <schema>.outbox now has
attempts, last_error, next_attempt_at and dead_at; the relay (libs/dataagent_db/outbox_relay.py)
may write those on a row it claimed that is neither published nor dead, and may insert one event
only, outbox.dead_letter on integration.outbox. integration.v_outbox_dead lists the dead events of
the caller's organisation for the Ops screen.

Fresh installs get the same from the generated dataagent.sql and dataagent_audit.sql (gen_ddl
outbox()); every statement here is idempotent, so it applies over either.

Revision ID: v3_0038
Revises: v3_0037
"""

import psycopg
import sqlalchemy as sa
from alembic import op

revision = "v3_0038"
down_revision = "v3_0037"
branch_labels = None
depends_on = None

# Applied to every <schema>.outbox the database has: 22 in dataagent, audit.outbox in its own.
EACH_OUTBOX = r"""
DO $$
DECLARE s text;
BEGIN
  FOR s IN SELECT table_schema FROM information_schema.tables
           WHERE table_name = 'outbox' AND table_type = 'BASE TABLE'
           ORDER BY table_schema
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.outbox ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0,'
      ' ADD COLUMN IF NOT EXISTS last_error text,'
      ' ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,'
      ' ADD COLUMN IF NOT EXISTS dead_at timestamptz', s);
    EXECUTE format('DROP INDEX IF EXISTS %I.outbox_unpublished_idx', s);
    EXECUTE format('CREATE INDEX outbox_unpublished_idx ON %I.outbox (created_at)'
                   ' WHERE published_at IS NULL AND dead_at IS NULL', s);
    EXECUTE format('GRANT SELECT, UPDATE (published_at, attempts, last_error, next_attempt_at,'
                   ' dead_at) ON %I.outbox TO svc_outbox_relay', s);
    EXECUTE format('DROP POLICY IF EXISTS outbox_relay_retry ON %I.outbox', s);
    EXECUTE format('CREATE POLICY outbox_relay_retry ON %I.outbox FOR UPDATE TO svc_outbox_relay'
                   ' USING (published_at IS NULL AND dead_at IS NULL)'
                   ' WITH CHECK (published_at IS NULL)', s);
  END LOOP;
END $$;
"""

DEAD_LETTER = r"""
GRANT INSERT ON integration.outbox TO svc_outbox_relay;
DROP POLICY IF EXISTS outbox_relay_dead_letter ON integration.outbox;
CREATE POLICY outbox_relay_dead_letter ON integration.outbox FOR INSERT TO svc_outbox_relay
  WITH CHECK (event_type = 'outbox.dead_letter');

DO $$
DECLARE body text;
BEGIN
  SELECT string_agg(format(
           'SELECT %L AS schema_name, id, org_id, aggregate_type, aggregate_id, event_type,'
           ' attempts, last_error, created_at, dead_at FROM %I.outbox WHERE dead_at IS NOT NULL'
           ' AND org_id = NULLIF(current_setting(''app.org_id'', true), '''')::uuid',
           table_schema, table_schema), ' UNION ALL ' ORDER BY table_schema)
    INTO body
    FROM information_schema.tables WHERE table_name = 'outbox' AND table_type = 'BASE TABLE';
  EXECUTE 'CREATE OR REPLACE VIEW integration.v_outbox_dead AS ' || body;
END $$;
GRANT SELECT ON integration.v_outbox_dead TO svc_integration, integration_read;
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            ("SET LOCAL ROLE da_owner;\n" + EACH_OUTBOX + DEAD_LETTER + "RESET ROLE;").replace(
                ":", "\\:"
            )
        )
    )
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        audit.execute("SET LOCAL ROLE da_owner;\n" + EACH_OUTBOX + "RESET ROLE;")


def downgrade() -> None:
    raise NotImplementedError("v3_0038 is not reversed")
