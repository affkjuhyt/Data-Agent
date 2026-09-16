"""grant the ANKT source permissions to roles that already manage warehouses

Revision ID: a6b7c8d9e0f1
Revises: f5b6c7d8e9f0
Create Date: 2026-09-16

Adding a name to the permission CATALOG grants it to nobody: the store reads
RolePermissions rows, so a screen guarded by a brand-new permission would 403
for every user on the day it shipped, including the administrator who would
have to go and grant it.

Derived from `viewWarehouses` rather than hard-coded to a role list because the
question "who manages external data connections here" is already answered by
that grant, and this deployment's answer may not be the default one. A source
and a warehouse are the same kind of responsibility.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "f5b6c7d8e9f0"
branch_labels = None
depends_on = None

_NEW = ("viewSources", "editSource", "runSource")


def upgrade() -> None:
    for action in _NEW:
        op.execute(
            f"""
            INSERT INTO "RolePermissions" (id, role_id, resource, action)
            SELECT gen_random_uuid(), role_id, 'sources', '{action}'
              FROM "RolePermissions"
             WHERE action = 'viewWarehouses'
            ON CONFLICT ON CONSTRAINT uq_role_permissions DO NOTHING
            """,
        )


def downgrade() -> None:
    actions = ", ".join(f"'{a}'" for a in _NEW)
    op.execute(f'DELETE FROM "RolePermissions" WHERE action IN ({actions})')
