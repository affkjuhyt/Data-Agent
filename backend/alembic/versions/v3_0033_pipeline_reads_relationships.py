"""svc_pipeline may read context.relationship (data mart bootstrap from FKs).

Creating a mart shapes dimensions and relations from the fact table's active relationships
(UC-06-06). Fresh installs get GRANT context_read TO svc_pipeline from dataagent.sql; this
revision is for clusters that already ran.

Revision ID: v3_0033
Revises: v3_0031
"""

from alembic import op

revision = "v3_0033"
down_revision = "v3_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT context_read TO svc_pipeline")


def downgrade() -> None:
    raise NotImplementedError("v3_0033 is not reversed")
