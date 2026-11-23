"""A dataset says whether its source's login can read it (DF-13).

A schema scan listed every table the catalogue showed, readable or not: an ingest job on a table
the login has no SELECT on failed at its first run. The scan now reads the right per table
(PostgreSQL's has_table_privilege, SQL Server's HAS_PERMS_BY_NAME, MySQL's SELECT grants) into
catalog.dataset.can_select; null where the engine does not say.

Revision ID: v3_0055
Revises: v3_0054
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0055"
down_revision = "v3_0054"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.dataset ADD COLUMN IF NOT EXISTS can_select boolean;
COMMENT ON COLUMN catalog.dataset.can_select IS 'DF-13: tài khoản của nguồn đọc được bảng (SELECT) ở lần quét gần nhất; null khi engine không cho biết (ClickHouse, quyền MySQL qua role)';

-- alembic writes alembic_version after this, as the migrating user: da_owner has no right on it.
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            SET LOCAL ROLE da_owner;
            ALTER TABLE catalog.dataset DROP COLUMN IF EXISTS can_select;
            RESET ROLE;
            """
        )
    )
