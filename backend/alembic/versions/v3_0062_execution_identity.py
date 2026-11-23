"""Who a query runs as (AE-17, ASK_ENGINE_SPEC §6.1, F-29).

A query reached a source with the source's own service account; the asker showed only in the
rewrite of the policies. catalog.source.role_mapping maps a user or a group to a role of a
PostgreSQL source: the executor runs that asker's query under SET LOCAL ROLE in its read-only
transaction, so the source's own grants and row security apply to them. query.query_run.executed_as
records the role every run used (the mapped role, the service account, or the workspace session's
role), for audit. An empty mapping keeps the service account.

Revision ID: v3_0062
Revises: v3_0061
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0062"
down_revision = "v3_0061"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.source ADD COLUMN IF NOT EXISTS role_mapping jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN catalog.source.role_mapping IS 'Danh tính thực thi (AE-17, F-29): {users: {user_id: role}, groups: {group_id: role}}; nguồn PostgreSQL chạy truy vấn của người được ánh xạ với SET LOCAL ROLE; rỗng là tài khoản dịch vụ như cũ. Thêm 23/11/2026';
-- query_run is partitioned by month: a column added to the parent is every partition's.
ALTER TABLE query.query_run ADD COLUMN IF NOT EXISTS executed_as text;
COMMENT ON COLUMN query.query_run.executed_as IS 'Role CSDL đã chạy truy vấn: role ánh xạ của người hỏi (SET LOCAL ROLE), tài khoản dịch vụ của nguồn, hoặc role của phiên workspace; null với lượt trước AE-17. Thêm 23/11/2026';

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
            ALTER TABLE query.query_run DROP COLUMN IF EXISTS executed_as;
            ALTER TABLE catalog.source DROP COLUMN IF EXISTS role_mapping;
            RESET ROLE;
            """
        )
    )
