"""Every answer says how far it can be trusted, as a code (AE-04, ARCHITECTURE §9.5).

qa.answer.trust_code holds the answer's label as one of VERIFIED, GOVERNED, DERIVED, EXPLORATORY,
DOCUMENT, INSUFFICIENT: the weakest of its figures and blocks, set by the worker. Until now the
label was a Vietnamese sentence inside the figures and blocks that the frontend mapped back to a
level. qa.answer.envelope keeps the answer's trust map (its path, each figure's and block's code,
why an agent turn stopped early). Both are nullable: answers written before this revision keep
neither.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent. The
table-level grants on qa.answer cover the new columns.

Revision ID: v3_0045
Revises: v3_0044
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0045"
down_revision = "v3_0044"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;

ALTER TABLE qa.answer ADD COLUMN IF NOT EXISTS trust_code text;
ALTER TABLE qa.answer ADD COLUMN IF NOT EXISTS envelope jsonb;
ALTER TABLE qa.answer DROP CONSTRAINT IF EXISTS answer_trust_code_check;
ALTER TABLE qa.answer ADD CONSTRAINT answer_trust_code_check
  CHECK (trust_code IS NULL OR trust_code IN
         ('VERIFIED','GOVERNED','DERIVED','EXPLORATORY','DOCUMENT','INSUFFICIENT'));
ALTER TABLE qa.answer DROP CONSTRAINT IF EXISTS answer_envelope_check;
ALTER TABLE qa.answer ADD CONSTRAINT answer_envelope_check
  CHECK (envelope IS NULL OR jsonb_typeof(envelope) = 'object');
COMMENT ON COLUMN qa.answer.trust_code IS
  'Nhãn tin cậy của cả câu trả lời: VERIFIED, GOVERNED, DERIVED, EXPLORATORY, DOCUMENT, INSUFFICIENT; nhãn yếu nhất trong các số và khối, do server đặt (AE-04, ARCHITECTURE §9.5, thêm 19/11/2026). Null: câu trả lời trước v3_0045';
COMMENT ON COLUMN qa.answer.envelope IS
  'Bản đồ tin cậy của câu trả lời (AE-04): đường trả lời, nhãn từng số và từng khối, lý do dừng sớm; phần còn lại của AnswerEnvelope đọc từ các cột khác (`worker_agent/answer/envelope.py`)';

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0045 is not reversed")
