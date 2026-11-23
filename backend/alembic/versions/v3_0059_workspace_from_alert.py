"""A workspace can be opened from an alert (AL-11).

"Mở điều tra" on an alert creates a workspace with created_from 'alert' and created_from_ref the
alert's id; the api seeds its params with the alert's metric and period and the plan the
investigation starts from (ANALYTICS_SPEC F-20 B.4). The CHECK on created_from gains 'alert'.

Revision ID: v3_0059
Revises: v3_0058
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0059"
down_revision = "v3_0058"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

-- The name Postgres gives the inline CHECK in dataagent.sql, so both databases match.
ALTER TABLE workspace.workspace DROP CONSTRAINT IF EXISTS workspace_created_from_check;
ALTER TABLE workspace.workspace ADD CONSTRAINT workspace_created_from_check
  CHECK (created_from IN ('blank','conversation','template','agent_run','alert'));
COMMENT ON COLUMN workspace.workspace.created_from IS 'blank, conversation, template, agent_run, alert (điều tra cảnh báo, created_from_ref là alert, thêm 23/11/2026)';

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
            ALTER TABLE workspace.workspace DROP CONSTRAINT IF EXISTS workspace_created_from_check;
            ALTER TABLE workspace.workspace ADD CONSTRAINT workspace_created_from_check
              CHECK (created_from IN ('blank','conversation','template','agent_run'));
            COMMENT ON COLUMN workspace.workspace.created_from IS 'blank, conversation, template, agent_run';
            RESET ROLE;
            """
        )
    )
