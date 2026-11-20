"""An agent's steps for an active pipeline wait in the Review Inbox (DF-17, AUD-04).

The data-prep agent wrote the steps a model proposed straight into the pipeline, an active and
scheduled one included. On an active pipeline they now open an inbox proposal of the new kind
pipeline_steps, applied when a reviewer accepts it (worker_agent/reviews.py).

Revision ID: v3_0046
Revises: v3_0045
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0046"
down_revision = "v3_0045"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER TABLE inbox.proposal DROP CONSTRAINT IF EXISTS proposal_kind_check;
ALTER TABLE inbox.proposal ADD CONSTRAINT proposal_kind_check
  CHECK (kind IN ('metric_change','synonym','relationship','example','glossary','schema_change',
                  'skill_publish','source_authority','writeback','document_validity',
                  'feedback_fix','knowledge_page','data_product','forecast_model',
                  'pipeline_steps'));
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0046 is not reversed")
