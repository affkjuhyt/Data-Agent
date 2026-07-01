"""auth_api_secrets_vault_schema

Gap fix discovered while extending OpenBao secrets storage to Snowflake/
warehouse credentials (PR review, see docs/Sandbox_SchemaCrawler_Implementation.md
§9): services/auth-api's live Secrets Vault feature (routers/secrets.py,
auth_core.py) has always queried lowercase, unquoted tables — api_keys,
secrets_vault, ip_allowlists, platform_settings, key_rotations — but no
migration in this chain ever created them. The only thing this chain DID
create for this feature is "SecretsVault" (PascalCase, via SecretsVaultORM /
op.create_table('SecretsVault', ...) in ff9ae8dcd7f7_initial_schema) — a
different Postgres identifier; unquoted SQL never reaches it.
SecretsVaultORM/SecretsVaultInsert/SecretsVaultDelete are dead code (grepped
repo-wide: referenced nowhere outside their own definitions) — this
migration does not touch/drop them, since removing a table some
not-yet-understood caller might reference is a separate, riskier decision
than today's additive fix.

Until this migration, every real deployment's /api/auth/secrets routes
would fail with "relation ... does not exist" the first time they ran
against actual Postgres — the feature only ever worked against
tests/integration/conftest.py's hand-rolled `_MINIMAL_SCHEMA` (test
container only, never applied to a real DB). This migration creates the
real tables matching that fixture's schema exactly (now the actual prod
source of truth — conftest.py's docstring claim "mirror" becomes literally
true instead of aspirational).

RLS: tenant_isolation on the 3 tables that carry tenant_id (api_keys,
secrets_vault, ip_allowlists) — platform_settings is platform-global,
key_rotations only carries secret_id (no direct tenant_id), same as the
test fixture's dynamic per-tenant_id-column loop. Explicit per-table block
(not relying on a1b2c3d4e5f6's dynamic discovery) since that migration
already ran earlier in the chain and won't retroactively pick up tables
added after it — same pattern as d4e5f6a7b8c9 / f6a7b8c9d0e1.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TENANT_SCOPED_TABLES = ("api_keys", "secrets_vault", "ip_allowlists")


def _apply_rls(table: str) -> str:
    return f"""
DO $$
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user;
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_isolation ON {table};
    CREATE POLICY tenant_isolation ON {table}
        AS PERMISSIVE
        FOR ALL
        TO PUBLIC
        USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
END
$$;
"""


def _revert_rls(table: str) -> str:
    return f"""
DO $$
BEGIN
    DROP POLICY IF EXISTS tenant_isolation ON {table};
    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
    REVOKE ALL ON {table} FROM app_user;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('key_hash', sa.Text(), nullable=False),
        sa.Column('key_prefix', sa.String(length=64), nullable=False),
        sa.Column('scope', sa.String(length=32), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rotated_from', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_api_keys_tenant', 'api_keys', ['tenant_id'])

    op.create_table(
        'secrets_vault',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('key_name', sa.String(length=255), nullable=False),
        sa.Column('key_type', sa.String(length=64), nullable=False),
        sa.Column('algorithm', sa.String(length=64), nullable=True),
        sa.Column('realm', sa.String(length=64), nullable=True),
        sa.Column('value_path', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('rotation_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_secrets_vault_tenant', 'secrets_vault', ['tenant_id'])

    op.create_table(
        'ip_allowlists',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('cidr', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ip_allowlists_tenant', 'ip_allowlists', ['tenant_id'])

    op.create_table(
        'platform_settings',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.execute("""
        INSERT INTO platform_settings (key, value) VALUES
            ('ip_allowlist_mode',    'allow_all'),
            ('vault_panic_mode',     'false'),
            ('vault_auto_rotation',  'false'),
            ('vault_pii_access_log', 'false')
        ON CONFLICT (key) DO NOTHING
    """)

    op.create_table(
        'key_rotations',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('secret_id', sa.UUID(), nullable=False),
        sa.Column('triggered_by', sa.String(length=32), nullable=False),
        sa.Column('actor_id', sa.String(length=255), nullable=True),
        sa.Column('old_version', sa.Integer(), nullable=True),
        sa.Column('new_version', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('access_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "triggered_by IN ('SCHEDULED','MANUAL','PANIC','REVEAL')",
            name='key_rotations_triggered_by_check',
        ),
        sa.ForeignKeyConstraint(['secret_id'], ['secrets_vault.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_key_rotations_secret', 'key_rotations', ['secret_id'])

    for table in _TENANT_SCOPED_TABLES:
        op.execute(_apply_rls(table))


def downgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.execute(_revert_rls(table))

    op.drop_index('ix_key_rotations_secret', table_name='key_rotations')
    op.drop_table('key_rotations')
    op.drop_table('platform_settings')
    op.drop_index('ix_ip_allowlists_tenant', table_name='ip_allowlists')
    op.drop_table('ip_allowlists')
    op.drop_index('ix_secrets_vault_tenant', table_name='secrets_vault')
    op.drop_table('secrets_vault')
    op.drop_index('ix_api_keys_tenant', table_name='api_keys')
    op.drop_table('api_keys')
