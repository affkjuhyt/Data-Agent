"""drop_members

"Members" was never written — no API, seed or job inserts into it, 0 rows on the
dev cluster on 02/10/2026 — yet workflow_runtime required an active row before
saving a conversation, so every real user's conversation went unsaved (only the
"anonymous" dev bypass got through). Tenant membership is already decided by
Keycloak: the tenant claim it issues on SSO approval is what require_tenant
verifies. Its role vocabulary (admin/editor/viewer) was a third one next to the
Keycloak roles and RolePermissions, and role_id was never set.

Downgrade recreates the table as 4e8a1c6d2b9f left it: tenant_isolation policy,
app_user grant and the two column comments. No rows are restored.

Revision ID: 6b2d8f4a1c7e
Revises: 4e8a1c6d2b9f
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "6b2d8f4a1c7e"
down_revision: str | None = "4e8a1c6d2b9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "Members"')


# One statement per op.execute: asyncpg rejects multi-statement strings.
_TENANT_RLS = (
    'ALTER TABLE "Members" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "Members" FORCE ROW LEVEL SECURITY',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON "Members" TO app_user',
    """CREATE POLICY tenant_isolation ON "Members" AS PERMISSIVE FOR ALL TO PUBLIC
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)""",
)


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def downgrade() -> None:
    op.create_table(
        "Members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "role_id",
            sa.SmallInteger(),
            nullable=True,
            comment="Never written or read; the role is tenant_role.",
        ),
        sa.Column(
            "tenant_role",
            sa.String(length=50),
            nullable=False,
            comment="admin, editor or viewer. workflow_runtime checks it before creating a conversation.",
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _timestamp("joined_at"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _timestamp("inserted_at"),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_members_tenant_user"),
    )
    op.create_index("idx_members_tenant", "Members", ["tenant_id"], unique=False)
    op.create_index(
        "idx_members_inserted_at_desc",
        "Members",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    for statement in _TENANT_RLS:
        op.execute(statement)
