"""panic_mode_per_tenant

Panic mode was one platform_settings flag. Triggering it for a tenant revoked
that tenant's secrets but set the global flag, so every tenant's secret routes
answered 503. It becomes compliance_settings.vault_panic_mode, per tenant.

A global flag that is on now is carried over failing closed: every tenant
starts in panic mode, and a platform-admin turns each one off. The
platform_settings row goes.

Revision ID: 1d8f3b5a7c9e
Revises: 9b6f2c4e8d1a
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "1d8f3b5a7c9e"
down_revision: str | None = "9b6f2c4e8d1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_COMMENT = (
    "Panic mode for this tenant: its secrets were revoked and its secret routes "
    "answer 503 until a platform-admin turns it off."
)
_SETTINGS_COMMENT_NOW = (
    "Platform-wide flags, key -> text (ip_allowlist_mode, vault_auto_rotation, "
    "vault_pii_access_log). Per-tenant settings are in PlatformSettings; panic mode is "
    "per tenant in compliance_settings."
)
_SETTINGS_COMMENT_BEFORE = (
    "Platform-wide flags, key -> text (ip_allowlist_mode, vault_panic_mode, "
    "vault_auto_rotation, vault_pii_access_log). Per-tenant settings are in PlatformSettings."
)


def upgrade() -> None:
    op.add_column(
        "compliance_settings",
        sa.Column(
            "vault_panic_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=_COLUMN_COMMENT,
        ),
    )
    op.execute(
        """
        INSERT INTO compliance_settings (tenant_id, vault_panic_mode)
        SELECT t.id::text, true FROM "Tenants" t
        WHERE EXISTS (SELECT 1 FROM platform_settings
                      WHERE key = 'vault_panic_mode' AND lower(value) = 'true')
        ON CONFLICT (tenant_id) DO UPDATE SET vault_panic_mode = true
        """
    )
    op.execute("DELETE FROM platform_settings WHERE key = 'vault_panic_mode'")
    op.execute(f"COMMENT ON TABLE platform_settings IS '{_SETTINGS_COMMENT_NOW}'")


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO platform_settings (key, value)
        SELECT 'vault_panic_mode',
               CASE WHEN EXISTS (SELECT 1 FROM compliance_settings WHERE vault_panic_mode)
                    THEN 'true' ELSE 'false' END
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """
    )
    op.drop_column("compliance_settings", "vault_panic_mode")
    op.execute(f"COMMENT ON TABLE platform_settings IS '{_SETTINGS_COMMENT_BEFORE}'")
