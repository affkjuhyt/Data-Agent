"""PlatformSettings: tenant-scoped JSON key/value store

Backs GET/PUT /api/platform/settings/{key} in workflow-runtime. One row per
(tenant, key), value is opaque JSONB so admin-UI screens can persist their
configuration without a per-screen migration.

Tenant isolation: the table carries a tenant_id column, so it gets the same
`tenant_isolation` RLS policy that a1b2c3d4e5f6 applied to every tenant table
(GRANT to app_user, ENABLE + FORCE RLS, USING/WITH CHECK on app.tenant_id).
That migration only covered tables that existed at the time, so a new tenant
table has to install the policy itself.

Revision ID: b5e7a9c1d3f0
Revises: f1r2e3s4h5n6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b5e7a9c1d3f0"
down_revision = "f1r2e3s4h5n6"
branch_labels = None
depends_on = None

_TABLE = "PlatformSettings"
_POLICY = "tenant_isolation"
_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("Tenants.id"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_platform_settings_tenant_key"),
    )
    op.create_index("idx_platformsettings_tenant", _TABLE, ["tenant_id"])

    # asyncpg rejects multiple statements per call — one DDL statement each.
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{_TABLE}" TO app_user')
    op.execute(f'ALTER TABLE "{_TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{_TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{_TABLE}"')
    op.execute(
        f'CREATE POLICY {_POLICY} ON "{_TABLE}" '
        f'AS PERMISSIVE FOR ALL TO PUBLIC '
        f'USING (tenant_id = {_TID}) WITH CHECK (tenant_id = {_TID})'
    )


def downgrade() -> None:
    op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{_TABLE}"')
    op.drop_index("idx_platformsettings_tenant", table_name=_TABLE)
    op.drop_table(_TABLE)
