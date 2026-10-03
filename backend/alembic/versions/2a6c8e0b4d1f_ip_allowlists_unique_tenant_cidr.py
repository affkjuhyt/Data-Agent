"""ip_allowlists_unique_tenant_cidr

The IP-allowlist create route answers 409 "CIDR already exists for this tenant" on a
unique violation, but nothing was unique: a second POST of the same CIDR stored a
duplicate rule, and toggling or deleting one copy left the other one in force. The
database now refuses the duplicate, which is what that 409 path always assumed.

Upgrade fails if duplicates already exist (dev had none on 2026-10-01): delete the
extra rows by hand first.

Revision ID: 2a6c8e0b4d1f
Revises: 7c3e5a9b1d2f
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2a6c8e0b4d1f"
down_revision: str | None = "7c3e5a9b1d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Same reasoning as b1d3f5a7c9e2: never queue DDL behind an idle transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ip_allowlists_tenant_cidr "
        "ON ip_allowlists (tenant_id, cidr)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ip_allowlists_tenant_cidr")
