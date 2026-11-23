"""Deleting an upload keeps its file: ingest.file_upload.deleted_at (DF-19, AUD-08).

DELETE /v1/uploads/{id} deleted the raw file from da-ingest with the row. It now marks the row
deleted and leaves the file, which the bucket's object lock keeps until its retention ends; the
dataset the upload created is disabled (worker_agent/uploads.deleted).

Revision ID: v3_0047
Revises: v3_0046
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0047"
down_revision = "v3_0046"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE ingest.file_upload ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
COMMENT ON COLUMN ingest.file_upload.deleted_at IS
  'Xoá mềm: tệp gốc giữ trong da-ingest tới hết retention (DF-19, AUD-08)';
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0047 is not reversed")
