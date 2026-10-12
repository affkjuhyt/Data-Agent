"""audit.export_job in the audit database (tab 3b D4.21, BR-138, decided 12/10/2026).

POST /v1/audit:export answers 202 with a job: the filters, the row count, a second approver past
100,000 rows, then a signed file in MinIO. The job lives next to the rows it exports, in
`<database>_audit`, owned by svc_audit like the rest of that database.

Fresh installs get the same table from the generated dataagent_audit.sql applied by v3_0002
(backend/schema/csdl/gen_ddl.py); this revision is idempotent so both paths end equal.

Revision ID: v3_0006
Revises: v3_0005
"""

import psycopg
from alembic import op

revision = "v3_0006"
down_revision = "v3_0005"
branch_labels = None
depends_on = None

DDL = """
SET LOCAL ROLE da_owner;
CREATE TABLE audit.export_job (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}',
  format text NOT NULL DEFAULT 'csv',
  row_count bigint,
  status text NOT NULL DEFAULT 'queued',
  requested_by uuid NOT NULL,
  approved_by uuid,
  approved_at timestamptz,
  hitl_request_id uuid,
  object_key text,
  signature text,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT export_job_pkey PRIMARY KEY (id),
  CHECK (format IN ('csv','jsonl')),
  CHECK (status IN ('queued','waiting_approval','running','ready','failed','expired')),
  CHECK (approved_by IS NULL OR approved_by <> requested_by),
  -- An uncounted job never runs: NULL would make the approval rule pass (BR-138).
  CHECK (status NOT IN ('running','ready') OR (row_count IS NOT NULL AND (row_count <= 100000 OR approved_by IS NOT NULL))),
  CHECK (status <> 'ready' OR (object_key IS NOT NULL AND signature IS NOT NULL))
);
CREATE INDEX export_job_requested_by_created_at_idx
  ON audit.export_job (requested_by, created_at DESC);
ALTER TABLE audit.export_job ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON audit.export_job
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON audit.export_job TO svc_audit;
GRANT SELECT ON audit.export_job TO audit_read;
RESET ROLE;
"""


def upgrade() -> None:
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        if audit.execute("SELECT to_regclass('audit.export_job') IS NULL").fetchone()[0]:
            audit.execute(DDL)


def downgrade() -> None:
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        rows = audit.execute(
            "SELECT CASE WHEN to_regclass('audit.export_job') IS NULL THEN 0"
            " ELSE (SELECT count(*) FROM audit.export_job) END"
        ).fetchone()[0]
        if rows:
            raise RuntimeError(f"audit.export_job holds {rows} jobs; not dropped")
        audit.execute("DROP TABLE IF EXISTS audit.export_job")
