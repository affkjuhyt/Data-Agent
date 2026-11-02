"""A conversation is titled by its first question (services/api/modules/qa/questions.py title_of).

Conversations were created without a title, so the Ask history listed every one as untitled. New
ones get it when they are created; this revision titles the older ones the same way: the first
question by seq, whitespace folded, cut at the last space within 80 characters with an ellipsis.
A conversation that already has a title keeps it. No column changes, so dataagent.sql is unchanged.

Revision ID: v3_0028
Revises: v3_0027
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0028"
down_revision = "v3_0027"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;
WITH opening AS (
  SELECT DISTINCT ON (conversation_id)
         conversation_id, btrim(regexp_replace(text, '\s+', ' ', 'g')) AS t
  FROM qa.question
  ORDER BY conversation_id, seq
)
UPDATE qa.conversation c
SET title = CASE
  WHEN length(f.t) <= 80 THEN f.t
  ELSE rtrim(left(coalesce(substring(left(f.t, 80) FROM '^(.*) '), left(f.t, 79)), 79)) || '…'
END
FROM opening f
WHERE c.id = f.conversation_id AND c.title IS NULL AND f.t <> '';
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0028 is not reversed")
