"""compliance_settings

auth_api created this table itself at startup (CREATE TABLE IF NOT EXISTS in
ensure_settings_table). That runtime DDL was removed with the SQLAlchemy rewrite, so
on a fresh database nothing created it: every compliance endpoint returned 500 and
grant-roles.sql's GRANT on it failed. Existing databases already have the table with
exactly this shape, hence IF NOT EXISTS.

Revision ID: 7c3e5a9b1d2f
Revises: 5b7d9f1a3c6e
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7c3e5a9b1d2f"
down_revision: str | None = "5b7d9f1a3c6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_settings (
            tenant_id           varchar(64)  PRIMARY KEY,
            pii_masking         boolean      NOT NULL DEFAULT false,
            pii_types           text         NOT NULL DEFAULT 'EMAIL,PHONE,CCCD,CMND,CREDIT_CARD,IP',
            residency_region    varchar(32)  NOT NULL DEFAULT 'Asia-SE1',
            residency_standards text         NOT NULL DEFAULT 'GDPR',
            retention_days      integer      NOT NULL DEFAULT 365,
            retention_lock      boolean      NOT NULL DEFAULT false,
            zero_trust          boolean      NOT NULL DEFAULT false,
            audit_residency     boolean      NOT NULL DEFAULT false,
            updated_at          timestamptz  DEFAULT now(),
            updated_by          varchar(128) DEFAULT ''
        )
        """
    )


def downgrade() -> None:
    # Not dropped: before this revision the table existed anyway (auth_api created it),
    # and dropping it would lose every tenant's compliance settings.
    pass
