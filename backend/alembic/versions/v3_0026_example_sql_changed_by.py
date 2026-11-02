"""context.example_query.sql_changed_by: who set or last changed the example's SQL.

Confirming an example is a two-person step, and the second person must differ from whoever wrote
the SQL, not only from whoever created the row: a steward can change another's SQL and then
confirm it. updated_by cannot say it (every patch overwrites it). Rows from before stay NULL,
which the check ignores.

Fresh installs get the column from the generated dataagent.sql; this revision is idempotent.

Revision ID: v3_0026
Revises: v3_0025
"""

from alembic import op

revision = "v3_0026"
down_revision = "v3_0025"
branch_labels = None
depends_on = None

DATAAGENT = """
SET LOCAL ROLE da_owner;
ALTER TABLE context.example_query ADD COLUMN IF NOT EXISTS sql_changed_by uuid;
COMMENT ON COLUMN context.example_query.sql_changed_by IS 'logic → org.app_user; người đặt hoặc sửa SQL gần nhất (khi tạo là created_by); người xác nhận ví dụ phải khác created_by và sql_changed_by trừ khi tổ chức bật tự duyệt. Khác updated_by, vốn đổi ở mọi lần sửa';
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(DATAAGENT)


def downgrade() -> None:
    raise NotImplementedError("v3_0026 is not reversed")
