"""kbdata_knowledge_base_id

Gap fix, same class of bug as f6a7b8c9d0e1 (KBWarehouse.tenant_id): KBDataORM
(basemodel...knowledge_base_orm.py) has always declared a `knowledge_base_id`
column (FK KBKnowledgeBase, nullable, indexed) and the KBKnowledgeBase table
itself exists (created alongside KBData in ff9ae8dcd7f7_initial_schema) — but
that migration never actually added the knowledge_base_id column onto the
real KBData table, and no later migration added it either. Any read/write
through KBDataORM that touches this column fails against the real schema
(asyncpg.UndefinedColumnError), independent of anything in this revision's
chain — e.g. GET /api/data/documents in kb_backend, which selects the column
unconditionally.

Column is nullable (no sensible default for a FK to an optional grouping) —
same precedent as KBWarehouse.tenant_id — so any already-existing KBData rows
are not broken by this upgrade. KBData already has the tenant_isolation RLS
policy from a1b2c3d4e5f6 / f1a2b3c4d5e6; this column doesn't change tenant
scoping, so no RLS changes are needed here.

Revision ID: d2e3f4a5b6c7
Revises: b5e7a9c1d3f0
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "b5e7a9c1d3f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBData",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_kbdata_knowledge_base", "KBData", "KBKnowledgeBase",
        ["knowledge_base_id"], ["knowledge_base_id"],
    )
    op.create_index("idx_kbdata_knowledge_base_id", "KBData", ["knowledge_base_id"])


def downgrade() -> None:
    op.drop_index("idx_kbdata_knowledge_base_id", table_name="KBData")
    op.drop_constraint("fk_kbdata_knowledge_base", "KBData", type_="foreignkey")
    op.drop_column("KBData", "knowledge_base_id")
