"""A work calendar says where its weeks and fiscal years start (GEN-21).

org.work_calendar gets week_start (ISO weekday, 1 = Monday), fiscal_year_start_month (1 = the
calendar year) and fiscal_year_named_by (end: July 2025 to June 2026 is FY2026; start: FY2025).
Until now a week, a quarter, a year and "from the start of the year" were the calendar year's,
whatever the customer closes its books on.

Fresh installs get the columns from the generated dataagent.sql; this revision is idempotent so
both paths end equal (the CHECKs take the names PostgreSQL gives the baseline's unnamed ones).

Revision ID: v3_0019
Revises: v3_0018
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0019"
down_revision = "v3_0018"
branch_labels = None
depends_on = None

COLUMNS = {
    "week_start": (
        "smallint NOT NULL DEFAULT 1",
        "week_start BETWEEN 1 AND 7",
        'Ngày đầu tuần theo ISO (1 = thứ Hai, 7 = Chủ nhật), cho grain tuần và "tuần này" (GEN-21)',
    ),
    "fiscal_year_start_month": (
        "smallint NOT NULL DEFAULT 1",
        "fiscal_year_start_month BETWEEN 1 AND 12",
        "Tháng bắt đầu năm tài chính (1 = năm dương lịch); luỹ kế từ đầu năm, quý và năm tài"
        " chính tính theo nó (GEN-21)",
    ),
    "fiscal_year_named_by": (
        "text NOT NULL DEFAULT 'end'",
        "fiscal_year_named_by IN ('start','end')",
        "Năm tài chính mang tên năm nó kết thúc (end: tháng 7/2025 đến 6/2026 là FY2026) hay năm nó"
        " bắt đầu (start)",
    ),
}

ADD = """
ALTER TABLE org.work_calendar ADD COLUMN IF NOT EXISTS {column} {kind};
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'org.work_calendar'::regclass
                    AND conname = 'work_calendar_{column}_check') THEN
    ALTER TABLE org.work_calendar ADD CONSTRAINT work_calendar_{column}_check CHECK ({check});
  END IF;
END $$;
COMMENT ON COLUMN org.work_calendar.{column} IS '{comment}';
"""


def upgrade() -> None:
    sql = ["SET LOCAL ROLE da_owner;"]
    for column, (kind, check, comment) in COLUMNS.items():
        sql.append(
            ADD.format(column=column, kind=kind, check=check, comment=comment.replace("'", "''"))
        )
    sql.append("RESET ROLE;")
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text("\n".join(sql).replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0019 is not reversed: it would drop columns that hold settings")
