"""A failed ingestion run says what kind of failure it was (RES-19, after Airbyte's failure types):
config (the person must fix something: credentials, rights, a table or column that is not there),
transient (a later run may pass: a timeout, a dropped connection, a busy source) or system (ours).

Fresh installs get the column from the generated dataagent.sql; this revision is idempotent so both
paths end equal.

Revision ID: v3_0029
Revises: v3_0028
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0029"
down_revision = "v3_0028"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE ingest.job_run ADD COLUMN IF NOT EXISTS failure_kind text;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'job_run_failure_kind_check'
      AND conrelid = 'ingest.job_run'::regclass
  ) THEN
    ALTER TABLE ingest.job_run
      ADD CONSTRAINT job_run_failure_kind_check
      CHECK (failure_kind IN ('config','transient','system'));
  END IF;
END $$;
COMMENT ON COLUMN ingest.job_run.failure_kind IS 'Loại lỗi khi failed (RES-19): config (người dùng phải sửa: thông tin đăng nhập, quyền, bảng hay cột không có), transient (thử lại được: hết giờ, mất kết nối, nguồn bận), system (lỗi của hệ thống)';
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0029 is not reversed")
