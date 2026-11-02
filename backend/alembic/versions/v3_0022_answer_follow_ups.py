"""An answer keeps the next questions it offers (the chat upgrade, worker_agent/suggestions.py).

qa.answer gets follow_ups, a JSON array of question texts. Each was read back through the
Understander and compiled before it was stored, so a click on one is answered rather than refused.

Fresh installs get the column from the generated dataagent.sql; this revision is idempotent so both
paths end equal.

Revision ID: v3_0022
Revises: v3_0021
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0022"
down_revision = "v3_0021"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE qa.answer ADD COLUMN IF NOT EXISTS follow_ups jsonb NOT NULL DEFAULT '[]';
COMMENT ON COLUMN qa.answer.follow_ups IS 'Câu hỏi tiếp theo câu trả lời gợi ý, mỗi câu đã đọc lại qua bộ hiểu và compile được (`worker_agent/suggestions.py`)';
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0022 is not reversed")
