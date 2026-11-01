"""How a Data Space, a document collection and a crawl source read what they ingest (GEN-11).

org.data_space, document.collection and acquire.crawl_source get ingest_profile, a JSON object:
locale, decimal_mark, date_order, ocr_languages. A key a level leaves out comes from the level
above it (collection or crawl source → Data Space → organization.locale), read by
libs/tabular.profile. Until now the readers were fixed to one market: OCR in vie+eng, crawled
figures with a decimal comma, dates day first, Accept-Language vi.

Fresh installs get the columns from the generated dataagent.sql; this revision is idempotent so
both paths end equal (the CHECK takes the name PostgreSQL gives the baseline's unnamed one).

Revision ID: v3_0018
Revises: v3_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0018"
down_revision = "v3_0017"
branch_labels = None
depends_on = None

TABLES = {
    "org.data_space": "organization.locale",
    "document.collection": "data_space.ingest_profile, rồi organization.locale",
    "acquire.crawl_source": "data_space.ingest_profile, rồi organization.locale",
}

COMMENT = (
    "Cách đọc dữ liệu nạp vào (GEN-11): locale, decimal_mark (. hoặc ,), date_order (dmy, mdy), "
    "ocr_languages (mã tesseract). Khoá không ghi lấy từ tầng trên: {above}"
)

ADD = """
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ingest_profile jsonb NOT NULL DEFAULT '{{}}';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = '{table}'::regclass AND conname = '{name}') THEN
    ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (jsonb_typeof(ingest_profile) = 'object');
  END IF;
END $$;
COMMENT ON COLUMN {table}.ingest_profile IS '{comment}';
"""


def upgrade() -> None:
    sql = ["SET LOCAL ROLE da_owner;"]
    for table, above in TABLES.items():
        sql.append(
            ADD.format(
                table=table,
                name=f"{table.split('.')[1]}_ingest_profile_check",
                comment=COMMENT.format(above=above).replace("'", "''"),
            )
        )
    sql.append("RESET ROLE;")
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text("\n".join(sql).replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0018 is not reversed: it would drop columns that hold settings")
