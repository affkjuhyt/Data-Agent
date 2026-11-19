"""Quality rules speak the expectation vocabulary: two new kinds and a GX-shaped result (DF-07).

A rule's kind maps to one Great Expectations expectation (libs/quality_vocab). Two have no
older kind: compound_unique (rows beyond the first of each key over several columns) and
match_regex (values that do not match a pattern). A result also keeps its outcome in GX's shape
(expectation, success, element_count, unexpected_count, unexpected_percent, mostly) in
ingest.quality_result.result; `violations` stays the count it was.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0044
Revises: v3_0043
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0044"
down_revision = "v3_0043"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;

ALTER TABLE ingest.quality_rule DROP CONSTRAINT IF EXISTS quality_rule_kind_check;
ALTER TABLE ingest.quality_rule ADD CONSTRAINT quality_rule_kind_check
  CHECK (kind IN ('not_null','range','unique','in_list','sum_matches','freshness','custom_sql',
                  'compound_unique','match_regex'));
COMMENT ON COLUMN ingest.quality_rule.params IS
  'Tham số của kind; chung: mostly (0 tới 1), row_condition (cột so hằng nối AND)';

ALTER TABLE ingest.quality_result ADD COLUMN IF NOT EXISTS result jsonb;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0044 is not reversed")
