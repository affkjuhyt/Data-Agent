"""unique_active_config_and_live_source_title

The single-active-row rules of the source and warehouse registries were only
indexes (non-unique) and a SELECT-then-INSERT title check, so two concurrent edits
could leave two active configs for one source/warehouse (the loser's changes
silently shadowed) and two sources could share a title (their Bronze documents are
named after it, so the second overwrites the first). The code now locks and checks;
these make the database refuse what the code should never produce.

Upgrade fails if such duplicates already exist: pick the survivor by hand first.

Revision ID: b1d3f5a7c9e2
Revises: a9c2e4f6b8d1
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b1d3f5a7c9e2"
down_revision: str | None = "a9c2e4f6b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kbsourceconfig_source_active")
    op.execute(
        "CREATE UNIQUE INDEX idx_kbsourceconfig_source_active "
        'ON "KBSourceConfig" (source_id) WHERE is_active = true'
    )
    op.execute("DROP INDEX IF EXISTS idx_kbwarehouseconfig_wh_active")
    op.execute(
        "CREATE UNIQUE INDEX idx_kbwarehouseconfig_wh_active "
        'ON "KBWarehouseConfig" (warehouse_id) WHERE is_active = true'
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kbsource_tenant_title_live "
        'ON "KBSource" (tenant_id, title) WHERE is_deleted = false'
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_kbsource_tenant_title_live")
    op.execute("DROP INDEX IF EXISTS idx_kbwarehouseconfig_wh_active")
    op.execute(
        "CREATE INDEX idx_kbwarehouseconfig_wh_active "
        'ON "KBWarehouseConfig" (warehouse_id) WHERE is_active = true'
    )
    op.execute("DROP INDEX IF EXISTS idx_kbsourceconfig_source_active")
    op.execute(
        "CREATE INDEX idx_kbsourceconfig_source_active "
        'ON "KBSourceConfig" (source_id) WHERE is_active = true'
    )
