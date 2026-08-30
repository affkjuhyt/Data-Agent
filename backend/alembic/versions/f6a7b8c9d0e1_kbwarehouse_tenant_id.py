"""kbwarehouse_tenant_id

Gap fix discovered while building the Schema Crawler Worker (sandbox_schema_docs.docx
§II), which reads tenant_id off "KBWarehouse" to scope crawled tables per
tenant: KBWarehouseORM (basemodel...knowledge_base_orm.py) has always
declared a `tenant_id` column (FK Tenants, nullable=False, indexed) and
backend/services/kb_backend/router/knowledge/warehouse_connect.py has always passed
tenant_id when inserting a KBWarehouse row — but ff9ae8dcd7f7_initial_schema
never actually created that column on the real table, and no later
migration added it either. Any insert into KBWarehouse with a tenant_id
field was failing against the real schema (UndefinedColumn) independent of
anything in this revision's chain; without this column, no Snowflake
connection could ever be registered for the crawler to find.

Column is nullable here (no sensible default for a FK) — same precedent as
fact_key in b2c3d4e5f6a7 — so any already-existing KBWarehouse rows in a
deployed environment aren't broken by this upgrade; RLS simply hides
tenant_id IS NULL rows from every tenant context (FORCE ROW LEVEL SECURITY
means even table owners don't bypass this).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_APPLY_RLS = r"""
DO $$
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON "KBWarehouse" TO app_user;
    ALTER TABLE "KBWarehouse" ENABLE ROW LEVEL SECURITY;
    ALTER TABLE "KBWarehouse" FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON "KBWarehouse"
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
    DROP POLICY IF EXISTS tenant_isolation ON "KBWarehouse";
    ALTER TABLE "KBWarehouse" NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE "KBWarehouse" DISABLE ROW LEVEL SECURITY;
    REVOKE ALL ON "KBWarehouse" FROM app_user;
END
$$;
"""


def upgrade() -> None:
    op.add_column('KBWarehouse', sa.Column('tenant_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_kbwarehouse_tenant', 'KBWarehouse', 'Tenants', ['tenant_id'], ['id'],
    )
    op.create_index('idx_kbwarehouse_tenant', 'KBWarehouse', ['tenant_id'])

    op.execute(_APPLY_RLS)


def downgrade() -> None:
    op.execute(_REVERT_RLS)

    op.drop_index('idx_kbwarehouse_tenant', table_name='KBWarehouse')
    op.drop_constraint('fk_kbwarehouse_tenant', 'KBWarehouse', type_='foreignkey')
    op.drop_column('KBWarehouse', 'tenant_id')
