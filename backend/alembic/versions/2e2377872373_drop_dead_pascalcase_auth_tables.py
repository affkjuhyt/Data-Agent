"""drop_dead_pascalcase_auth_tables

"APIKeys", "IPAllowlists", "SecretsVault" and "KeyRotations" come from the
initial schema, but auth_api has always read and written the snake_case copies
(api_keys, ip_allowlists, secrets_vault, key_rotations; see b8c9d0e1f2a3's
docstring). No service references the PascalCase tables, and all four had 0
rows on the dev cluster on 27/09/2026. Their ORM classes and pydantic models
are removed in the same commit (docs/v2/10-database-design.md §2.3, §5).

Downgrade recreates them as the initial schema did, including the
tenant_isolation policy a1b2c3d4e5f6 applied to every tenant_id table.

Revision ID: 2e2377872373
Revises: c2d4e6f8a0b1
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "2e2377872373"
down_revision: str | None = "c2d4e6f8a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Children before parents: KeyRotations -> SecretsVault, APIKeys self-FK.
    for table in ("KeyRotations", "SecretsVault", "APIKeys", "IPAllowlists"):
        op.execute(f'DROP TABLE IF EXISTS "{table}"')


# One statement per op.execute: asyncpg rejects multi-statement strings.
_TENANT_RLS = (
    'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "{t}" FORCE ROW LEVEL SECURITY',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON "{t}" TO app_user',
    """CREATE POLICY tenant_isolation ON "{t}" AS PERMISSIVE FOR ALL TO PUBLIC
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)""",
)


def downgrade() -> None:
    op.create_table(
        "APIKeys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rotated_from", sa.UUID(), nullable=True),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from"],
            ["APIKeys.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["Tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
        sa.UniqueConstraint("tenant_id", "key_prefix", name="uq_api_keys_tenant_prefix"),
    )
    op.create_index(
        "idx_api_keys_inserted_at_desc",
        "APIKeys",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_api_keys_key_hash", "APIKeys", ["key_hash"], unique=False)
    op.create_index("idx_api_keys_tenant", "APIKeys", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_APIKeys_tenant_id"), "APIKeys", ["tenant_id"], unique=False)

    op.create_table(
        "IPAllowlists",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("cidr", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["Tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_ip_allowlists_inserted_at_desc",
        "IPAllowlists",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_ip_allowlists_tenant", "IPAllowlists", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_IPAllowlists_tenant_id"), "IPAllowlists", ["tenant_id"], unique=False)

    op.create_table(
        "SecretsVault",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_name", sa.String(length=255), nullable=False),
        sa.Column("key_type", sa.String(length=50), nullable=False),
        sa.Column("algorithm", sa.String(length=50), nullable=True),
        sa.Column("realm", sa.String(length=100), nullable=True),
        sa.Column("openbao_path", sa.Text(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("rotation_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["Tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "key_name", "version", name="uq_secrets_vault_tenant_name"
        ),
    )
    op.create_index(
        "idx_secrets_vault_inserted_at_desc",
        "SecretsVault",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_SecretsVault_tenant_id"), "SecretsVault", ["tenant_id"], unique=False)

    op.create_table(
        "KeyRotations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("secret_id", sa.UUID(), nullable=False),
        sa.Column("triggered_by", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("old_version", sa.SmallInteger(), nullable=True),
        sa.Column("new_version", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["secret_id"],
            ["SecretsVault.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_key_rotations_inserted_at_desc",
        "KeyRotations",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_key_rotations_secret", "KeyRotations", ["secret_id"], unique=False)
    op.create_index(op.f("ix_KeyRotations_secret_id"), "KeyRotations", ["secret_id"], unique=False)

    for table in ("APIKeys", "IPAllowlists", "SecretsVault"):
        for statement in _TENANT_RLS:
            op.execute(statement.format(t=table))
