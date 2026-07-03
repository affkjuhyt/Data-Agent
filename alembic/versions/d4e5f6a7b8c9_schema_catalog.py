"""schema_catalog

Implements sandbox_schema_docs.docx Phần II — the auto-crawled Schema
Catalog. Distinct from SchemaMemory (c3d4e5f6a7b8): that table is
hand-written by an Admin and read by exact match; this one is written by
the schema-crawler-worker background job from Snowflake INFORMATION_SCHEMA
metadata + an LLM-generated business description, with a content-hash
(schema_hash) for incremental re-crawl (doc §2.7) and a Qdrant point id for
the paired "schema_catalog" vector collection (doc §2.5).

Same tenant_isolation RLS policy as every other tenant-scoped table (see
b2c3d4e5f6a7 / c3d4e5f6a7b8 for the pattern this copies).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_APPLY_RLS = r"""
DO $$
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON "SchemaCatalog" TO app_user;
    ALTER TABLE "SchemaCatalog" ENABLE ROW LEVEL SECURITY;
    ALTER TABLE "SchemaCatalog" FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON "SchemaCatalog"
        AS PERMISSIVE
        FOR ALL
        TO PUBLIC
        USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
END
$$;
"""

_REVERT_RLS = r"""
DO $$
BEGIN
    DROP POLICY IF EXISTS tenant_isolation ON "SchemaCatalog";
    ALTER TABLE "SchemaCatalog" NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE "SchemaCatalog" DISABLE ROW LEVEL SECURITY;
    REVOKE ALL ON "SchemaCatalog" FROM app_user;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        'SchemaCatalog',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('warehouse_id', sa.UUID(), nullable=False),
        sa.Column('schema_name', sa.String(length=128), nullable=False),
        sa.Column('table_name', sa.String(length=128), nullable=False),
        sa.Column('columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('last_altered', sa.DateTime(timezone=True), nullable=True),
        sa.Column('text_description', sa.Text(), nullable=False),
        sa.Column('join_hints', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('schema_hash', sa.String(length=32), nullable=False),
        sa.Column('qdrant_point_id', sa.String(length=128), nullable=True),
        sa.Column('last_crawled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.ForeignKeyConstraint(['warehouse_id'], ['KBWarehouse.warehouse_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'warehouse_id', 'schema_name', 'table_name', name='uq_schemacatalog_table'),
    )
    op.create_index('idx_schemacatalog_lookup', 'SchemaCatalog', ['tenant_id', 'warehouse_id'])

    op.execute(_APPLY_RLS)


def downgrade() -> None:
    op.execute(_REVERT_RLS)

    op.drop_index('idx_schemacatalog_lookup', table_name='SchemaCatalog')
    op.drop_table('SchemaCatalog')
