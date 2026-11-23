"""An alert keeps the figures that fired it (AL-10).

The monitor runtime (worker_agent/monitor_runs.py) writes one alert per firing with every member
past the rule: its value, what it was compared to (the threshold or the period before), the
change, and the query_run that read it, so the alert can be traced back to a recorded query
(ANALYTICS_SPEC F-20 B.3). value/threshold_value hold one number each and cannot carry a split.

Revision ID: v3_0056
Revises: v3_0055
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0056"
down_revision = "v3_0055"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE monitor.alert ADD COLUMN IF NOT EXISTS figures jsonb NOT NULL DEFAULT '[]';
COMMENT ON COLUMN monitor.alert.figures IS 'Số đã quyết định alert, mỗi phần tử một thành viên: member, value, compared_to (ngưỡng hoặc số kỳ trước), change, query_run_id; ghi bởi runtime theo dõi (AL-10). Thêm 23/11/2026';

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
            ALTER TABLE monitor.alert DROP COLUMN IF EXISTS figures;
            RESET ROLE;
            """
        )
    )
