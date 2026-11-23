"""A column keeps the evidence of its personal-data label (DF-11).

A profile scan labelled a column from a sample of its values (libs/pii.column_decision) and kept
the label only: a steward confirming it could not see what it rested on. column_meta.pii_evidence
keeps the type found, its share of the sampled values, how many distinct values carried it, how
many were checked, and the scan run that read them.

Revision ID: v3_0052
Revises: v3_0051
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0052"
down_revision = "v3_0051"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.column_meta ADD COLUMN IF NOT EXISTS pii_evidence jsonb;
COMMENT ON COLUMN catalog.column_meta.pii_evidence IS 'DF-11: bằng chứng của nhãn ở lần quét gần nhất {pii_type, share, distinct_hits, samples_checked, scan_run_id}';

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
            ALTER TABLE catalog.column_meta DROP COLUMN IF EXISTS pii_evidence;
            RESET ROLE;
            """
        )
    )
