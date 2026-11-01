"""A metric version names the time it is read along, at any grain the compiler cuts by (GEN-22).

context.metric_version gets time_dimension_id (logic → semantic_dimension): the order date or the
ship date of a sale, or a star schema's date table. Until now a metric was read along the one time
dimension of its own table, and a fact keyed to a date table had none. time_grain also takes the
week and the fiscal quarter and year (GEN-21).

Fresh installs get both from the generated dataagent.sql; this revision is idempotent so both paths
end equal (the CHECK keeps the name PostgreSQL gave the baseline's unnamed one).

Revision ID: v3_0020
Revises: v3_0019
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0020"
down_revision = "v3_0019"
branch_labels = None
depends_on = None

COMMENT = (
    "logic → semantic_dimension; chiều thời gian chỉ số đọc theo (GEN-22), ví dụ ngày giao hàng"
    " thay cho ngày đặt; trống thì chiều thời gian của bảng, hoặc của bảng ngày nó nối tới"
)
GRAINS = "'day','week','month','quarter','year','fiscal_quarter','fiscal_year'"

SQL = f"""
SET LOCAL ROLE da_owner;
ALTER TABLE context.metric_version ADD COLUMN IF NOT EXISTS time_dimension_id uuid;
COMMENT ON COLUMN context.metric_version.time_dimension_id IS '{COMMENT}';
ALTER TABLE context.metric_version DROP CONSTRAINT IF EXISTS metric_version_time_grain_check;
ALTER TABLE context.metric_version
  ADD CONSTRAINT metric_version_time_grain_check CHECK (time_grain IN ({GRAINS}));
COMMENT ON COLUMN context.metric_version.time_grain IS
  'day, week, month, quarter, year, fiscal_quarter, fiscal_year';
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0020 is not reversed: it would drop a metric's time dimension")
