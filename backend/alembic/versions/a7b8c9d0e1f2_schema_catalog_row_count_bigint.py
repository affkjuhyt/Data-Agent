"""schema_catalog_row_count_bigint

Gap fix flagged in PR review on d4e5f6a7b8c9: "SchemaCatalog".row_count was
declared sa.Integer(), capping at ~2.1B rows. Snowflake tables (the only
source this column is populated from, see snowflake_metadata.py's
discover_and_describe()) routinely exceed that — Integer silently truncates/
errors on overflow on write. BigInteger matches what the source system can
actually report.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'SchemaCatalog', 'row_count',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'SchemaCatalog', 'row_count',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
