"""The dynamic agent's long-term memory of a person in a Data Space (worker_agent/agentic/memory.py,
after Memori): qa.memory_fact holds what they said that should outlive the conversation, their
presentation preferences, the definitions they use, durable facts about their work. Never a
figure from a query, never a secret. A thing said again bumps times and last_seen_at.

Fresh installs get the table from the generated dataagent.sql; this revision is idempotent so both
paths end equal.

Revision ID: v3_0027
Revises: v3_0026
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0027"
down_revision = "v3_0026"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
CREATE TABLE IF NOT EXISTS qa.memory_fact (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  data_space_id uuid NOT NULL,
  user_id uuid NOT NULL,
  kind text NOT NULL,
  content text NOT NULL,
  content_hash text NOT NULL,
  times integer NOT NULL DEFAULT 1,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  source_question_id uuid,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid,
  CONSTRAINT memory_fact_pkey PRIMARY KEY (id),
  CHECK (kind IN ('preference','definition','fact','feedback')),
  CHECK (status IN ('active','archived')),
  CHECK (times >= 1)
);
COMMENT ON TABLE qa.memory_fact IS 'Trí nhớ dài hạn của agent về một người dùng trong một Data Space (sau Memori): sở thích trình bày, định nghĩa người dùng tự nêu, sự kiện bền về công việc của họ. Trích từ hội thoại sau mỗi lượt, không bao giờ chứa số liệu truy vấn hay bí mật; gộp trùng theo content_hash, đếm lần nhắc lại (`worker_agent/agentic/memory.py`).';
COMMENT ON COLUMN qa.memory_fact.data_space_id IS 'Data Space nơi trí nhớ được nói ra';
COMMENT ON COLUMN qa.memory_fact.user_id IS 'Người dùng mà trí nhớ thuộc về; chỉ người đó được truy hồi';
COMMENT ON COLUMN qa.memory_fact.kind IS 'preference, definition, fact, feedback';
COMMENT ON COLUMN qa.memory_fact.content IS 'Một câu, ngôn ngữ người dùng';
COMMENT ON COLUMN qa.memory_fact.content_hash IS 'SHA-256 của nội dung đã chuẩn hoá: cùng ý nói lại thì tăng times, không thêm dòng';
COMMENT ON COLUMN qa.memory_fact.times IS 'Số lượt hội thoại đã nói ý này';
COMMENT ON COLUMN qa.memory_fact.last_seen_at IS 'Lần gần nhất ý này được nói';
COMMENT ON COLUMN qa.memory_fact.source_question_id IS 'logic → qa.question: câu hỏi đầu tiên nói ra ý này';
COMMENT ON COLUMN qa.memory_fact.status IS 'active, archived';

CREATE UNIQUE INDEX IF NOT EXISTS memory_fact_data_space_id_user_id_content_hash_key ON qa.memory_fact (data_space_id, user_id, content_hash);
CREATE INDEX IF NOT EXISTS memory_fact_user_id_data_space_id_last_seen_at_idx ON qa.memory_fact (user_id, data_space_id, last_seen_at DESC) WHERE status = 'active';
ALTER TABLE qa.memory_fact ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON qa.memory_fact;
CREATE POLICY org_isolation ON qa.memory_fact USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON qa.memory_fact TO svc_qa;
GRANT SELECT ON qa.memory_fact TO qa_read;
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0027 is not reversed")
