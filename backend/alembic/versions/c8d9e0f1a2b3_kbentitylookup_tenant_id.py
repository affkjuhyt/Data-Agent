"""kbentitylookup tenant_id

Gap fix, same class of bug as a5f7b3d9c1e4 / f74ea70fb743: KBEntityLookupORM
(postgres_orm/knowledge_base_orm.py) has always declared a `tenant_id` column
(FK -> Tenants.id, nullable, indexed) plus a composite index on
(tenant_id, alias_name) — but ff9ae8dcd7f7_initial_schema's
`op.create_table('KBEntityLookup', ...)` never actually included the column
or that index. This is on the hot path of EVERY gold promotion (entity-alias
resolution runs per chunk): every single chunk has always failed to commit
during bronze->silver->gold promotion with
"asyncpg.exceptions.UndefinedColumnError: column KBEntityLookup.tenant_id
does not exist" the moment gold promotion tried to look up/write an entity
alias, which rolled back the whole per-chunk atomic commit (Postgres text
block + Qdrant point) for every chunk of every document ever promoted to
gold — meaning no gold-tier document has ever actually gotten its chunks
persisted to KBTextBlock/KBTextBlockVersion or indexed into Qdrant, despite
`current_tier` being flipped to 'gold' and the lifecycle history row
reporting (falsely) that the promotion ran.

Column is nullable (NULL = global seed alias shared by all tenants, per the
ORM docstring) — additive, doesn't affect any existing row.

Revision ID: c8d9e0f1a2b3
Revises: f74ea70fb743
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "f74ea70fb743"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBEntityLookup",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "KBEntityLookup_tenant_id_fkey",
        "KBEntityLookup", "Tenants",
        ["tenant_id"], ["id"],
    )
    op.create_index(
        "ix_KBEntityLookup_tenant_id",
        "KBEntityLookup", ["tenant_id"],
    )
    op.create_index(
        "idx_kbentitylookup_tenant_alias",
        "KBEntityLookup", ["tenant_id", "alias_name"],
    )


def downgrade() -> None:
    op.drop_index("idx_kbentitylookup_tenant_alias", table_name="KBEntityLookup")
    op.drop_index("ix_KBEntityLookup_tenant_id", table_name="KBEntityLookup")
    op.drop_constraint("KBEntityLookup_tenant_id_fkey", "KBEntityLookup", type_="foreignkey")
    op.drop_column("KBEntityLookup", "tenant_id")
