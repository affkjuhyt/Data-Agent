"""A forecast model version is proposed in the inbox, and publishing clears its experimental label.

inbox.proposal.kind gains forecast_model (UC-15-04). forecast.prediction stays append-only except
is_experimental, which comes off when the version is published (BR-158).

Fresh installs get both from the generated dataagent.sql; this revision is idempotent so both
paths end equal.

Revision ID: v3_0031
Revises: v3_0030
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0031"
down_revision = "v3_0030"
branch_labels = None
depends_on = None

KINDS = (
    "'metric_change','synonym','relationship','example','glossary','schema_change',"
    "'skill_publish','source_authority','writeback','document_validity','feedback_fix',"
    "'knowledge_page','data_product','forecast_model'"
)
COMMENT = (
    "metric_change, synonym, relationship, example, glossary, schema_change, skill_publish, "
    "source_authority, writeback, document_validity, feedback_fix, data_product (kích hoạt hoặc "
    "đổi phiên bản data product, UC-07-13), knowledge_page, forecast_model (công bố phiên bản "
    "mô hình dự báo, UC-15-04)"
)

SQL = f"""
SET LOCAL ROLE da_owner;
DO $body$
DECLARE
  constraint_name text;
BEGIN
  FOR constraint_name IN
    SELECT c.conname
    FROM pg_constraint AS c
    JOIN pg_attribute AS a
      ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.conrelid = 'inbox.proposal'::regclass
      AND c.contype = 'c'
      AND a.attname = 'kind'
  LOOP
    EXECUTE format('ALTER TABLE inbox.proposal DROP CONSTRAINT %I', constraint_name);
  END LOOP;
END
$body$;
ALTER TABLE inbox.proposal
  ADD CONSTRAINT proposal_kind_check CHECK (kind IN ({KINDS}));
COMMENT ON COLUMN inbox.proposal.kind IS '{COMMENT}';
GRANT UPDATE (is_experimental) ON forecast.prediction TO svc_forecast;
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0031 is not reversed")
