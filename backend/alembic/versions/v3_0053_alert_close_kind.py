"""An alert says how it was closed: resolved or a false alarm.

POST /v1/alerts/{id}:close kept only a note, so "Đóng do báo nhầm" on the alert page could not be
stored and a false alarm read as any other close: nothing could learn from it (BR-124).
monitor.alert.close_kind is resolved or false_positive while the alert is closed, null otherwise.

Revision ID: v3_0053
Revises: v3_0052
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0053"
down_revision = "v3_0052"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE monitor.alert ADD COLUMN IF NOT EXISTS close_kind text;
-- The name Postgres gives the same inline CHECK in dataagent.sql, so both databases match.
ALTER TABLE monitor.alert DROP CONSTRAINT IF EXISTS alert_close_kind_check;
ALTER TABLE monitor.alert
  ADD CONSTRAINT alert_close_kind_check CHECK (close_kind IN ('resolved','false_positive'));
COMMENT ON COLUMN monitor.alert.close_kind IS 'Cách đóng: resolved (đã xử lý) hoặc false_positive (báo nhầm, để agent học); null khi chưa đóng. Thêm 23/11/2026';

-- alembic writes alembic_version after this, as the migrating user: da_owner has no right on it.
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            SET LOCAL ROLE da_owner;
            ALTER TABLE monitor.alert DROP CONSTRAINT IF EXISTS alert_close_kind_check;
            ALTER TABLE monitor.alert DROP COLUMN IF EXISTS close_kind;
            RESET ROLE;
            """
        )
    )
