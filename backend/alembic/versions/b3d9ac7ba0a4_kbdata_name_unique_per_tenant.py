"""kbdata_name_unique_per_tenant

The initial schema created "KBData" with UNIQUE(name) over the whole table, so
two tenants could not both have a document called e.g. "Bản tin nợ công". The
ORM (knowledge_base_orm.py) and every caller (kb_backend document_ops /
ingestion, web_crawler bronze) already assume the per-tenant
`idx_kbdata_tenant_name`, but no migration ever created it. The two keyset
pagination indexes the ORM declares were likewise never migrated.

Swapping the constraint cannot fail on existing data: rows unique by name are
unique by (tenant_id, name). Downgrade can fail if two tenants now share a
name, which is the point of the upgrade.

Revision ID: b3d9ac7ba0a4
Revises: 2e2377872373
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b3d9ac7ba0a4"
down_revision: str | None = "2e2377872373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_kbdata_tenant_name ON "KBData" (tenant_id, name)'
    )
    op.execute('ALTER TABLE "KBData" DROP CONSTRAINT IF EXISTS "KBData_name_key"')
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kbdata_keyset "
        'ON "KBData" (tenant_id, current_tier, inserted_at, data_id)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS idx_kbdata_keyset_all ON "KBData" (tenant_id, inserted_at, data_id)'
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kbdata_keyset_all")
    op.execute("DROP INDEX IF EXISTS idx_kbdata_keyset")
    op.execute('ALTER TABLE "KBData" ADD CONSTRAINT "KBData_name_key" UNIQUE (name)')
    op.execute("DROP INDEX IF EXISTS idx_kbdata_tenant_name")
