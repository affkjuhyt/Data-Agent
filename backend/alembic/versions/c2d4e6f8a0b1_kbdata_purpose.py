"""kbdata_purpose

Phase D: a `purpose` column on "KBData" — 'training' | 'production' |
'evaluation', a steward tag set at ingestion time. Non-production data is
hidden from RAG/search by default (see qdrant_connector.py's
_build_qdrant_filters); this migration only adds the column + index, no RLS
policy changes needed ("KBData" already has row-level tenant isolation from
an earlier migration, which covers any new column automatically).

Revision ID: c2d4e6f8a0b1
Revises: b3c5d7e9f1a2
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, None] = "b3c5d7e9f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBData",
        sa.Column("purpose", sa.String(16), nullable=False, server_default="production"),
    )
    op.create_index("idx_kbdata_tenant_purpose", "KBData", ["tenant_id", "purpose"])


def downgrade() -> None:
    op.drop_index("idx_kbdata_tenant_purpose", table_name="KBData")
    op.drop_column("KBData", "purpose")
