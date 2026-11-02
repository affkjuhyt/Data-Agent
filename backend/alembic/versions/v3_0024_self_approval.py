"""Self-approval is an organisation's switch, and the database guards two people only while it is off.

org.organization.allow_self_approval (default false) lets one person both make and approve. Until
now ten CHECKs refused the same person on both sides of an approval; a CHECK cannot read the
organisation, so they are replaced by org.two_people(), a trigger that does, and an existing row
stays editable after the switch is turned off (only a changed pair is judged).

audit.export_job lives in the audit database, which cannot read org.organization: it gets
self_approved, set by the API when it lets a requester approve their own export, and the CHECK
reads that.

Fresh installs get all of it from the generated dataagent.sql / dataagent_audit.sql; this revision
is idempotent so both paths end equal. Unnamed CHECKs of the baseline are found by definition.

Revision ID: v3_0024
Revises: v3_0023
"""

import psycopg
import sqlalchemy as sa
from alembic import op

revision = "v3_0024"
down_revision = "v3_0023"
branch_labels = None
depends_on = None

DATAAGENT = """
SET LOCAL ROLE da_owner;
ALTER TABLE org.organization ADD COLUMN IF NOT EXISTS allow_self_approval boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN org.organization.allow_self_approval IS 'Tổ chức cho phép một người vừa tạo vừa duyệt (tự duyệt). Tắt: trigger org.two_people từ chối cặp người trùng nhau ở các bảng duyệt; bật: cho qua. Không đổi các dòng đã có';

DO $$
DECLARE
  c record;
  p record;
BEGIN
  FOR p IN SELECT * FROM (VALUES
    ('catalog.dataset_version', 'certified_by <> created_by'),
    ('context.knowledge_page_version', 'reviewed_by <> created_by'),
    ('context.data_product', 'owner_id <> steward_id'),
    ('context.data_product', 'activated_by <> created_by'),
    ('pipeline.writeback_request', 'approved_by <> requested_by'),
    ('acquire.crawl_source', 'legal_reviewed_by <> owner_id'),
    ('forecast.model_version', 'approved_by <> created_by'),
    ('forecast.formula_component', 'certified_by <> created_by')
  ) AS v(tbl, pattern) LOOP
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = p.tbl::regclass AND contype = 'c'
                AND pg_get_constraintdef(oid) ~ p.pattern
    LOOP
      EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', p.tbl, c.conname);
    END LOOP;
  END LOOP;

  -- The frontier rule keeps everything but the two-people conjunct.
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'governance.model_provider'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ 'approved_by <> enabled_by'
  LOOP
    EXECUTE format('ALTER TABLE governance.model_provider DROP CONSTRAINT %I', c.conname);
    ALTER TABLE governance.model_provider ADD CONSTRAINT model_provider_frontier_check CHECK (
      NOT (kind = 'frontier' AND enabled)
      OR (zero_retention AND enabled_by IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL));
  END LOOP;

  -- Only the equality depends on the switch: a certified version still needs a creator.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'catalog.dataset_version'::regclass AND contype = 'c'
                    AND pg_get_constraintdef(oid) ~ 'certified_by IS NULL\\) OR \\(created_by IS NOT NULL') THEN
    ALTER TABLE catalog.dataset_version ADD CONSTRAINT dataset_version_certifier_created_check
      CHECK (certified_by IS NULL OR created_by IS NOT NULL);
  END IF;

  -- The certifier is recorded with both columns and a certified version has one; that it is a
  -- third person moved to the trigger.
  IF EXISTS (SELECT 1 FROM pg_constraint
              WHERE conrelid = 'context.knowledge_page_version'::regclass
                AND conname = 'knowledge_page_version_certifier_check'
                AND pg_get_constraintdef(oid) ~ 'DISTINCT FROM') THEN
    ALTER TABLE context.knowledge_page_version DROP CONSTRAINT knowledge_page_version_certifier_check;
    ALTER TABLE context.knowledge_page_version ADD CONSTRAINT knowledge_page_version_certifier_check CHECK (
      (certified_by IS NULL) = (certified_at IS NULL)
      AND (status <> 'certified' OR certified_by IS NOT NULL));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION org.self_approval_allowed(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT COALESCE((SELECT allow_self_approval FROM org.organization WHERE id = p_org), false)
$$;
REVOKE EXECUTE ON FUNCTION org.self_approval_allowed(uuid) FROM PUBLIC;

-- TG_ARGV is column-name pairs a, b, a, b, ... Each pair must hold two different people while the
-- organisation has not allowed self-approval. A NULL side is never refused (no person yet). On an
-- UPDATE only a pair that changed is judged, so turning the switch off leaves existing rows
-- editable: their other columns can still change. A pair naming a column the row does not have is
-- a mistake in the CREATE TRIGGER, and raises instead of silently guarding nothing.
-- SECURITY DEFINER so that it runs as the owner, which may call self_approval_allowed. Because it
-- then reads any organisation's flag for whatever table it is attached to, PUBLIC may not attach
-- it (Postgres checks EXECUTE at CREATE TRIGGER, not when the trigger fires), and it refuses any
-- table outside the list below as well.
CREATE OR REPLACE FUNCTION org.two_people() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  i int := 0;
  a text;
  b text;
  n jsonb := to_jsonb(NEW);
  o jsonb := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END;
BEGIN
  IF TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME NOT IN (
       'catalog.dataset_version', 'context.knowledge_page_version', 'context.data_product',
       'pipeline.writeback_request', 'acquire.crawl_source', 'forecast.model_version',
       'forecast.formula_component', 'governance.model_provider') THEN
    RAISE EXCEPTION 'org.two_people không gắn được vào bảng %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME;
  END IF;
  IF TG_NARGS % 2 <> 0 THEN
    RAISE EXCEPTION 'org.two_people nhận cặp tên cột, nhận % đối số', TG_NARGS;
  END IF;
  WHILE i < TG_NARGS LOOP
    a := TG_ARGV[i];
    b := TG_ARGV[i + 1];
    i := i + 2;
    IF NOT (n ? a AND n ? b) THEN
      RAISE EXCEPTION 'org.two_people: bảng % không có cột % hoặc %', TG_TABLE_NAME, a, b;
    END IF;
    IF o IS NOT NULL AND n ->> a IS NOT DISTINCT FROM o ->> a AND n ->> b IS NOT DISTINCT FROM o ->> b THEN
      CONTINUE;
    END IF;
    IF n ->> a IS NOT NULL AND n ->> a = n ->> b AND NOT org.self_approval_allowed(NEW.org_id) THEN
      RAISE EXCEPTION 'Cần hai người khác nhau (% và %): tổ chức chưa bật tự duyệt', a, b
        USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  RETURN NEW;
END
$$;
REVOKE EXECUTE ON FUNCTION org.two_people() FROM PUBLIC;

DROP TRIGGER IF EXISTS dataset_version_two_people ON catalog.dataset_version;
CREATE TRIGGER dataset_version_two_people BEFORE INSERT OR UPDATE ON catalog.dataset_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('certified_by', 'created_by');
DROP TRIGGER IF EXISTS knowledge_page_version_two_people ON context.knowledge_page_version;
CREATE TRIGGER knowledge_page_version_two_people BEFORE INSERT OR UPDATE ON context.knowledge_page_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('reviewed_by', 'created_by', 'certified_by', 'created_by', 'certified_by', 'reviewed_by');
DROP TRIGGER IF EXISTS data_product_two_people ON context.data_product;
CREATE TRIGGER data_product_two_people BEFORE INSERT OR UPDATE ON context.data_product
  FOR EACH ROW EXECUTE FUNCTION org.two_people('owner_id', 'steward_id', 'activated_by', 'created_by');
DROP TRIGGER IF EXISTS writeback_request_two_people ON pipeline.writeback_request;
CREATE TRIGGER writeback_request_two_people BEFORE INSERT OR UPDATE ON pipeline.writeback_request
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'requested_by');
DROP TRIGGER IF EXISTS crawl_source_two_people ON acquire.crawl_source;
CREATE TRIGGER crawl_source_two_people BEFORE INSERT OR UPDATE ON acquire.crawl_source
  FOR EACH ROW EXECUTE FUNCTION org.two_people('legal_reviewed_by', 'owner_id');
DROP TRIGGER IF EXISTS model_version_two_people ON forecast.model_version;
CREATE TRIGGER model_version_two_people BEFORE INSERT OR UPDATE ON forecast.model_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'created_by');
DROP TRIGGER IF EXISTS formula_component_two_people ON forecast.formula_component;
CREATE TRIGGER formula_component_two_people BEFORE INSERT OR UPDATE ON forecast.formula_component
  FOR EACH ROW EXECUTE FUNCTION org.two_people('certified_by', 'created_by');
DROP TRIGGER IF EXISTS model_provider_two_people ON governance.model_provider;
CREATE TRIGGER model_provider_two_people BEFORE INSERT OR UPDATE ON governance.model_provider
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'enabled_by');
RESET ROLE;
"""

AUDIT = """
SET LOCAL ROLE da_owner;
ALTER TABLE audit.export_job ADD COLUMN IF NOT EXISTS self_approved boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN audit.export_job.self_approved IS 'API đặt true khi tổ chức bật tự duyệt và requested_by cũng là approved_by; csdl kiểm audit không đọc được org.organization nên API là người gác';
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'audit.export_job'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ 'approved_by <> requested_by'
              AND pg_get_constraintdef(oid) !~ 'self_approved'
  LOOP
    EXECUTE format('ALTER TABLE audit.export_job DROP CONSTRAINT %I', c.conname);
    ALTER TABLE audit.export_job ADD CONSTRAINT export_job_self_approved_check
      CHECK (approved_by IS NULL OR approved_by <> requested_by OR self_approved);
  END LOOP;
END $$;
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(DATAAGENT.replace(":", "\\:")))
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        audit.execute(AUDIT)


def downgrade() -> None:
    raise NotImplementedError("v3_0024 is not reversed")
