"""Design decisions of 12/10/2026 (tab 3, 3b), on clusters already migrated.

- audit.outbox in the audit database: every schema has an outbox (tab 3 D1 item 13), audit's
  included, so the Audit service can open the second approval of a >100,000-row export (BR-138).
- conflict.source_authority keeps its active ranking while an edit waits as a draft (UC-07-05):
  one active and one draft per (data_space_id, data_kind), the old active becomes superseded.
- catalog.source.writeback_tables: the target tables a writeback may write (UC-06-04).
- pipeline.writeback_request may be undo_requested, so an undo is not asked twice.
- pipeline.data_product.owner_id: the person who approves publishing (UC-06-06).
- ingest.quality_result.acknowledged_at, acknowledged_note: the reason to keep using data that
  broke a high-severity rule (BR-116).

Fresh installs get all of it from the generated dataagent.sql and dataagent_audit.sql; this
revision is idempotent so both paths end equal.

Revision ID: v3_0009
Revises: v3_0008
"""

import psycopg
from alembic import op

revision = "v3_0009"
down_revision = "v3_0008"
branch_labels = None
depends_on = None

# Unnamed constraints of the baseline are found by their definition, not a guessed name.
DROP_CHECK = """
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = '{table}'::regclass AND contype IN ('c', 'u')
              AND pg_get_constraintdef(oid) ~ '{pattern}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;
"""

DATAAGENT = (
    "SET LOCAL ROLE da_owner;"
    # catalog.source.writeback_tables
    """
ALTER TABLE catalog.source ADD COLUMN IF NOT EXISTS writeback_tables text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN catalog.source.writeback_tables IS 'Bảng đích được phép ghi ngược (schema.bảng); rỗng là không bảng nào (UC-06-04, thêm 12/10/2026)';
"""
    # conflict.source_authority: draft beside active
    + DROP_CHECK.format(
        table="conflict.source_authority", pattern="^UNIQUE \\(data_space_id, data_kind\\)$"
    )
    + DROP_CHECK.format(table="conflict.source_authority", pattern="status")
    + """
ALTER TABLE conflict.source_authority
  ADD CONSTRAINT source_authority_status_check CHECK (status IN ('draft','active','superseded')),
  ADD CONSTRAINT source_authority_approved_check
    CHECK (status NOT IN ('active','superseded') OR approved_by IS NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS source_authority_data_space_id_data_kind_key
  ON conflict.source_authority (data_space_id, data_kind) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS source_authority_data_space_id_data_kind_key_2
  ON conflict.source_authority (data_space_id, data_kind) WHERE status = 'draft';
CREATE OR REPLACE FUNCTION conflict.source_authority_supersede() RETURNS trigger
LANGUAGE plpgsql AS $f$
BEGIN
  UPDATE conflict.source_authority SET status = 'superseded', updated_at = now()
   WHERE data_space_id = NEW.data_space_id AND data_kind = NEW.data_kind
     AND status = 'active' AND id <> NEW.id;
  RETURN NEW;
END $f$;
DROP TRIGGER IF EXISTS source_authority_supersede ON conflict.source_authority;
CREATE TRIGGER source_authority_supersede BEFORE UPDATE OF status ON conflict.source_authority
  FOR EACH ROW WHEN (NEW.status = 'active' AND OLD.status IS DISTINCT FROM 'active')
  EXECUTE FUNCTION conflict.source_authority_supersede();
"""
    # pipeline.writeback_request: undo_requested
    + DROP_CHECK.format(table="pipeline.writeback_request", pattern="undone")
    + """
ALTER TABLE pipeline.writeback_request ADD CONSTRAINT writeback_request_status_check
  CHECK (status IN ('draft','previewed','submitted','approved','rejected','scheduled','executing',
                    'executed','cancelled_mismatch','failed','undo_requested','undone'));
"""
    # pipeline.data_product.owner_id: the approver of a published version is its owner
    + """
ALTER TABLE pipeline.data_product ADD COLUMN IF NOT EXISTS owner_id uuid;
UPDATE pipeline.data_product SET owner_id = coalesce(approved_by, created_by) WHERE owner_id IS NULL;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pipeline.data_product WHERE owner_id IS NULL) THEN
    RAISE EXCEPTION 'pipeline.data_product rows without approver or creator: set owner_id first';
  END IF;
END $$;
ALTER TABLE pipeline.data_product ALTER COLUMN owner_id SET NOT NULL;
COMMENT ON COLUMN pipeline.data_product.owner_id IS 'logic → org.app_user; người phụ trách duyệt phát hành, khác người soạn (thêm 12/10/2026)';
"""
    + DROP_CHECK.format(table="pipeline.data_product", pattern="approved_by = owner_id")
    + """
ALTER TABLE pipeline.data_product ADD CONSTRAINT data_product_owner_approves
  CHECK (approved_by IS NULL OR approved_by = owner_id);
"""
    # ingest.quality_result: the acknowledgement's time and reason
    + """
ALTER TABLE ingest.quality_result ADD COLUMN IF NOT EXISTS acknowledged_at timestamptz,
                                  ADD COLUMN IF NOT EXISTS acknowledged_note text;
UPDATE ingest.quality_result
   SET acknowledged_at = coalesce(acknowledged_at, checked_at),
       acknowledged_note = coalesce(acknowledged_note, 'Xác nhận trước 12/10/2026, không ghi lý do')
 WHERE acknowledged_by IS NOT NULL;
"""
    + DROP_CHECK.format(table="ingest.quality_result", pattern="acknowledged_note")
    + """
ALTER TABLE ingest.quality_result ADD CONSTRAINT quality_result_acknowledged_check
  CHECK (acknowledged_by IS NULL OR (acknowledged_at IS NOT NULL AND acknowledged_note IS NOT NULL));
COMMENT ON COLUMN ingest.quality_result.acknowledged_at IS 'Thêm 12/10/2026';
COMMENT ON COLUMN ingest.quality_result.acknowledged_note IS 'Lý do dùng tiếp, bắt buộc khi có acknowledged_by (BR-116, thêm 12/10/2026)';
RESET ROLE;
"""
)

AUDIT_OUTBOX = """
SET LOCAL ROLE da_owner;
CREATE TABLE audit.outbox (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  CONSTRAINT outbox_pkey PRIMARY KEY (id)
);
CREATE INDEX outbox_unpublished_idx ON audit.outbox (created_at) WHERE published_at IS NULL;
ALTER TABLE audit.outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON audit.outbox
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON audit.outbox TO svc_audit;
GRANT USAGE ON SCHEMA audit TO svc_outbox_relay;
GRANT SELECT, UPDATE (published_at) ON audit.outbox TO svc_outbox_relay;
CREATE POLICY outbox_relay_read ON audit.outbox FOR SELECT TO svc_outbox_relay USING (true);
CREATE POLICY outbox_relay_publish ON audit.outbox FOR UPDATE TO svc_outbox_relay
  USING (published_at IS NULL) WITH CHECK (published_at IS NOT NULL);
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(DATAAGENT)
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        if audit.execute("SELECT to_regclass('audit.outbox') IS NULL").fetchone()[0]:
            audit.execute(AUDIT_OUTBOX)


def downgrade() -> None:
    raise NotImplementedError("v3_0009 is not reversed: it would drop columns that hold data")
