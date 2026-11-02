"""svc_audit_worker may set audit.export_job.self_approved.

worker-agent releases an approved audit export by writing approved_by; when the approver is the
requester (the org's self-approval switch was on when the API let them decide) the CHECK
export_job_self_approved_check wants self_approved in the same UPDATE, and the role could not
write that column, so such an export never left waiting_approval.

Fresh installs get the grant from the generated dataagent_audit.sql; this revision is idempotent.

Revision ID: v3_0025
Revises: v3_0024
"""

import psycopg
from alembic import op

revision = "v3_0025"
down_revision = "v3_0024"
branch_labels = None
depends_on = None

AUDIT = """
SET LOCAL ROLE da_owner;
GRANT UPDATE (status, approved_by, approved_at, self_approved, updated_at)
  ON audit.export_job TO svc_audit_worker;
RESET ROLE;
"""


def upgrade() -> None:
    url = op.get_bind().engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        audit.execute(AUDIT)


def downgrade() -> None:
    raise NotImplementedError("v3_0025 is not reversed")
