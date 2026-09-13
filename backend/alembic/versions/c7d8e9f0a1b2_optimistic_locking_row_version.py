"""optimistic_locking_row_version

Adds row_version + updated_at to the tables kb-backend read-modify-writes.

Revision ID: c7d8e9f0a1b2
Revises: e1f2a3b4c5d6
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "KBData",
    "KBFilterPolicy",
    "KBExtractionPolicy",
    "KBTextTable",
    "KBWarehouseConfig",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column(
            "row_version", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ))
        op.add_column(table, sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "updated_at")
        op.drop_column(table, "row_version")
