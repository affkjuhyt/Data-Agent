"""A measure declared additive whose SQL is not can be corrected from the Review Inbox (AL-03).

The chart validator (worker_agent/answer/chart_check.py) blocks a share or a stacked total of a
measure that does not add up. When the measure was declared `full` or `semi` over a COUNT
DISTINCT or a ratio, it opens a proposal of the new kind measure_additivity; accepted, the
steward's declaration becomes `none` (worker_agent/reviews.py).

Revision ID: v3_0057
Revises: v3_0056
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0057"
down_revision = "v3_0056"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE inbox.proposal DROP CONSTRAINT IF EXISTS proposal_kind_check;
ALTER TABLE inbox.proposal ADD CONSTRAINT proposal_kind_check
  CHECK (kind IN ('metric_change','synonym','relationship','example','glossary','schema_change',
                  'skill_publish','source_authority','writeback','document_validity',
                  'feedback_fix','knowledge_page','data_product','forecast_model',
                  'pipeline_steps','measure_additivity'));
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


# The kind list before: a proposal of the new kind must be gone first, or the CHECK refuses.
DOWN = """
SET LOCAL ROLE da_owner;
ALTER TABLE inbox.proposal DROP CONSTRAINT IF EXISTS proposal_kind_check;
ALTER TABLE inbox.proposal ADD CONSTRAINT proposal_kind_check
  CHECK (kind IN ('metric_change','synonym','relationship','example','glossary','schema_change',
                  'skill_publish','source_authority','writeback','document_validity',
                  'feedback_fix','knowledge_page','data_product','forecast_model',
                  'pipeline_steps'));
RESET ROLE;
"""


def downgrade() -> None:
    op.execute(sa.text(DOWN))
