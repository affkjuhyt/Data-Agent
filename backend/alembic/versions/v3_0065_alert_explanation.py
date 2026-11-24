"""An alert keeps the paragraph the model wrote to explain it (F-20).

The monitor runtime (worker_agent/monitor_runs.py) decides an alert by its rules; only then, as
invariant 11 allows, the model writes a short paragraph from the alert's own figures, and every
number the paragraph holds is checked against them before it is stored. Null when no model was
reachable or no sentence survived the check: the summary still says what changed.

Revision ID: v3_0065
Revises: v3_0062
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0065"
down_revision = "v3_0062"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE monitor.alert ADD COLUMN IF NOT EXISTS explanation text;
COMMENT ON COLUMN monitor.alert.explanation IS 'Đoạn giải thích do mô hình viết sau khi luật đã quyết có alert (bất biến 11), chỉ dùng số của figures; null khi chưa viết được hoặc không có mô hình (F-20). Thêm 24/11/2026';

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
            ALTER TABLE monitor.alert DROP COLUMN IF EXISTS explanation;
            RESET ROLE;
            """
        )
    )
