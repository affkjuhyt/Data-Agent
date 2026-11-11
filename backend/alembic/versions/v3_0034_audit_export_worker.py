"""svc_audit_worker writes audit exports, not only releases them.

POST /v1/audit:export answered 202 and nothing wrote the file: worker-agent's role in the audit
database could read export_job and move a waiting job to queued or failed, nothing else. It now
reads audit_log (the rows a job names), writes the job's object_key, signature and expires_at,
and steps a job queued → running → ready/failed (services/worker_agent/audit_exports.py). The
restrictive policy worker_decision_only becomes worker_export_steps, and the trigger
export_job_worker_step allows exactly those steps, the approver's decision included.

Fresh installs get the same from the generated dataagent_audit.sql (routines/audit.sql); this
revision is idempotent so it applies over either.

Revision ID: v3_0034
Revises: v3_0033
"""

import psycopg
from alembic import op

revision = "v3_0034"
down_revision = "v3_0033"
branch_labels = None
depends_on = None

AUDIT = r"""
SET LOCAL ROLE da_owner;
GRANT SELECT ON audit.audit_log TO svc_audit_worker;
GRANT UPDATE (status, approved_by, approved_at, self_approved, object_key, signature, expires_at, updated_at)
  ON audit.export_job TO svc_audit_worker;
DROP POLICY IF EXISTS worker_decision_only ON audit.export_job;
DROP POLICY IF EXISTS worker_export_steps ON audit.export_job;
CREATE POLICY worker_export_steps ON audit.export_job AS RESTRICTIVE FOR UPDATE TO svc_audit_worker
  USING (status IN ('waiting_approval', 'queued', 'running'))
  WITH CHECK (status IN ('queued', 'running', 'ready', 'failed'));
CREATE OR REPLACE FUNCTION audit.export_job_worker_step() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_user <> 'svc_audit_worker' THEN
    RETURN NEW;
  END IF;
  IF (OLD.status, NEW.status) IN (('waiting_approval', 'queued'), ('waiting_approval', 'failed')) THEN
    IF (NEW.status = 'queued' AND (NEW.approved_by IS NULL OR NEW.approved_at IS NULL))
       OR (NEW.object_key, NEW.signature, NEW.expires_at)
          IS DISTINCT FROM (OLD.object_key, OLD.signature, OLD.expires_at) THEN
      RAISE EXCEPTION 'Duyệt bản xuất kiểm toán chỉ ghi người duyệt (BR-138)'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  IF (OLD.status, NEW.status) IN (('queued', 'running'), ('queued', 'failed'),
                                  ('running', 'ready'), ('running', 'failed')) THEN
    IF (NEW.approved_by, NEW.approved_at, NEW.self_approved)
       IS DISTINCT FROM (OLD.approved_by, OLD.approved_at, OLD.self_approved)
       OR (NEW.status = 'ready' AND NEW.expires_at IS NULL)
       OR (NEW.status <> 'ready' AND (NEW.object_key, NEW.signature, NEW.expires_at)
           IS DISTINCT FROM (OLD.object_key, OLD.signature, OLD.expires_at)) THEN
      RAISE EXCEPTION 'Bản xuất kiểm toán: chỉ bản sẵn sàng mới ghi tệp, và không đổi người duyệt (BR-138)'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Bản xuất kiểm toán không chuyển được từ % sang % (BR-138)', OLD.status, NEW.status
    USING ERRCODE = 'insufficient_privilege';
END
$$;
DROP TRIGGER IF EXISTS export_job_worker_step ON audit.export_job;
CREATE TRIGGER export_job_worker_step BEFORE UPDATE ON audit.export_job
  FOR EACH ROW EXECUTE FUNCTION audit.export_job_worker_step();
RESET ROLE;
"""


def upgrade() -> None:
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        audit.execute(AUDIT)


def downgrade() -> None:
    raise NotImplementedError("v3_0034 is not reversed")
