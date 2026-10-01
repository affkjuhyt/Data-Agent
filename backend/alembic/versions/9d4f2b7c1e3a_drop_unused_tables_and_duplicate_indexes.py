"""drop_unused_tables_and_duplicate_indexes

Four tables nothing reads or writes, all 0 rows on the dev cluster on
01/10/2026:

  * "SchemaMemory" — hand-written table notes; its Admin API and reader were
    removed earlier, text_to_sql reads "SchemaCatalog" or introspects.
  * "Tools" and "AgentTools" — no API, seed or runtime reader. Tools are set
    per canvas node (allowedBuiltinTools in config_loader), not per row here.
  * "KeycloakRealmConfigs" — the realm comes from KC_REALM and the token/JWKS
    URLs from env; the audit bridge stopped reading it (AuditLogs worker).

Eleven duplicate indexes: the ORM declared both a named Index("idx_...") and
index=True on the same column, so ff9ae8dcd7f7 created two identical btree
indexes. The ix_* twins go (the ORM drops index=True); on "Tenants".slug the
UNIQUE constraint's own index stays and idx_tenants_slug goes. The "Tools"
pair goes with its table.

Downgrade recreates all of it as it was at 7c3e5a9b1d2f, including the
tenant_isolation policies and app_user grants. No rows are restored.

Revision ID: 9d4f2b7c1e3a
Revises: 2a6c8e0b4d1f
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9d4f2b7c1e3a"
down_revision: str | None = "2a6c8e0b4d1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children before parents.
_DROPPED = ("AgentTools", "Tools", "SchemaMemory", "KeycloakRealmConfigs")

# (index, table, column) — the ix_* twin of an identical idx_* index.
_DUPLICATE_INDEXES = (
    ("ix_Agents_tenant_id", "Agents", "tenant_id"),
    ("ix_KBConnections_tenant_id", "KBConnections", "tenant_id"),
    ("ix_MCP_tenant_id", "MCP", "tenant_id"),
    ("ix_Members_tenant_id", "Members", "tenant_id"),
    ("ix_SystemPrompts_tenant_id", "SystemPrompts", "tenant_id"),
    ("ix_AgentMemories_tenant_id", "AgentMemories", "tenant_id"),
    ("ix_MemoryPolicy_agent_id", "MemoryPolicy", "agent_id"),
    ("ix_Workflows_agent_id", "Workflows", "agent_id"),
    ("ix_Conversations_tenant_id", "Conversations", "tenant_id"),
)


def upgrade() -> None:
    for table in _DROPPED:
        op.execute(f'DROP TABLE IF EXISTS "{table}"')
    for index, _, _ in _DUPLICATE_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS "{index}"')
    op.execute("DROP INDEX IF EXISTS idx_tenants_slug")


# One statement per op.execute: asyncpg rejects multi-statement strings.
_TENANT_RLS = (
    'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "{t}" FORCE ROW LEVEL SECURITY',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON "{t}" TO app_user',
    """CREATE POLICY tenant_isolation ON "{t}" AS PERMISSIVE FOR ALL TO PUBLIC
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)""",
)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _timestamp(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=_now(), nullable=False)


def downgrade() -> None:
    op.create_index("idx_tenants_slug", "Tenants", ["slug"], unique=True)
    for index, table, column in _DUPLICATE_INDEXES:
        op.create_index(index, table, [column], unique=False)

    op.create_table(
        "KeycloakRealmConfigs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("realm_name", sa.String(length=100), nullable=False),
        sa.Column("keycloak_base_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(length=100), nullable=False),
        sa.Column("client_secret_ref", sa.Text(), nullable=False),
        sa.Column("jwks_url", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("token_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("inserted_at"),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("tenant_id"),
        sa.UniqueConstraint("realm_name"),
    )
    op.create_index(
        "idx_keycloakrealmconfigs_inserted_at_desc",
        "KeycloakRealmConfigs",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )

    op.create_table(
        "SchemaMemory",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("data_source", sa.String(length=64), nullable=False),
        sa.Column("table_name", sa.String(length=128), nullable=False),
        sa.Column("column_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("business_description", sa.Text(), nullable=False),
        sa.Column(
            "access_roles",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::character varying[]"),
            nullable=False,
        ),
        _timestamp("updated_at"),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "data_source", "table_name", name="uq_schemamemory_table"),
    )
    op.create_index(
        "idx_schemamemory_lookup", "SchemaMemory", ["tenant_id", "data_source"], unique=False
    )

    op.create_table(
        "Tools",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        _timestamp("created_at"),
        _timestamp("inserted_at"),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tools_tenant", "Tools", ["tenant_id"], unique=False)
    op.create_index("ix_Tools_tenant_id", "Tools", ["tenant_id"], unique=False)
    op.create_index(
        "idx_tools_inserted_at_desc",
        "Tools",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )

    op.create_table(
        "AgentTools",
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("tool_id", sa.UUID(), nullable=False),
        _timestamp("inserted_at"),
        sa.ForeignKeyConstraint(["tool_id"], ["Tools.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["AgentVersions.id"]),
        sa.PrimaryKeyConstraint("version_id", "tool_id"),
    )

    for table in ("KeycloakRealmConfigs", "SchemaMemory", "Tools"):
        for statement in _TENANT_RLS:
            op.execute(statement.format(t=table))
