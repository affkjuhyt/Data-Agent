"""kblifecyclehistory pipeline_metadata

Gap fix, same class of bug as a5f7b3d9c1e4 / d2e3f4a5b6c7: KBLifecycleHistoryORM
(postgres_orm/knowledge_base_orm.py) has always declared a `pipeline_metadata`
JSONB column, and every bronze/silver/gold transition write
(backend/services/kb_backend/pipeline/ingestion/bronze.py's upload_to_bronze,
among others) has always populated it — but ff9ae8dcd7f7_initial_schema's
`op.create_table('KBLifecycleHistory', ...)` never actually included the
column. Unlike the other gap-fix migrations found so far, this one is on the
hot path of every single document upload (not a rarely-hit feature): any
POST /api/pipeline/ingestion/bronze has always 500'd with
"asyncpg.exceptions.UndefinedColumnError: column pipeline_metadata of
relation KBLifecycleHistory does not exist" the moment it tried to record
the bronze-transition history row, immediately after the KBData row itself
(a separate, already-committed insert) succeeded — hence orphaned KBData
rows with zero matching KBLifecycleHistory rows being the first visible
symptom.

Column is nullable (no sensible default for freeform per-transition
context) — same precedent as the other additive gap-fix columns this
session — so it doesn't affect any existing row.

Revision ID: f74ea70fb743
Revises: a5f7b3d9c1e4
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f74ea70fb743"
down_revision: Union[str, None] = "a5f7b3d9c1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBLifecycleHistory",
        sa.Column("pipeline_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("KBLifecycleHistory", "pipeline_metadata")
