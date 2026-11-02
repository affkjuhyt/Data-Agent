"""An answer keeps the blocks the agent composed it from (the dynamic analyst agent,
worker_agent/agentic/).

qa.answer gets blocks, a JSON array of typed blocks in the order the agent chose: markdown, kpi,
chart, table, insights, callout, code, follow_ups, caveats. The data of a chart, table or KPI was
filled by the server from the turn's datasets, never written by the model. An empty array is an
answer of the older shape, which the api still projects into blocks.

Fresh installs get the column from the generated dataagent.sql; this revision is idempotent so both
paths end equal.

Revision ID: v3_0023
Revises: v3_0022
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0023"
down_revision = "v3_0022"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE qa.answer ADD COLUMN IF NOT EXISTS blocks jsonb NOT NULL DEFAULT '[]';
COMMENT ON COLUMN qa.answer.blocks IS 'Khối câu trả lời do agent tự soạn theo thứ tự (markdown, kpi, chart, table, insights, callout, code, follow_ups, caveats); dữ liệu của chart/table/kpi do server điền từ dataset (`worker_agent/agentic/blocks.py`). Rỗng: câu trả lời theo khuôn cũ';
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0023 is not reversed")
