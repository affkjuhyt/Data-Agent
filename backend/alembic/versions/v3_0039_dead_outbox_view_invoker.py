"""integration.v_outbox_dead reads with its reader's rights, and no service role reads it (PL-02).

v3_0038 made it an owner's-rights view granted to svc_integration and integration_read: any session
of the organisation with that role read the dead events of every outbox, exec's included, whose
row policies keep each owner's vault to them, and their last_error, past the grants of those
schemas (security review of 08a72d97). It is now security_invoker, so each outbox's grants and row
policies apply to whoever reads it, and the two grants are revoked: an operator reads it as the
database's administrator (RUNBOOK §10b) until the Ops screen has a role of its own.

Revision ID: v3_0039
Revises: v3_0038
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0039"
down_revision = "v3_0038"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
ALTER VIEW integration.v_outbox_dead SET (security_invoker = true);
REVOKE SELECT ON integration.v_outbox_dead FROM svc_integration, integration_read;
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0039 is not reversed")
