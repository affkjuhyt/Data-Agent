"""One computation of an answer, recorded so a replay can check it (AL-01, F-09, BR-177).

qa.compute_run: the code that worked out an answer's derived figures (its sha256), the inputs it
read (query runs, snapshots, with their hashes), what came out (sha256, row count) and how long it
took. The governed path writes one with every answer that has derived figures; the receipt's
figures of source_kind compute_run point at it. Append-only: a trigger refuses UPDATE and DELETE
and svc_qa keeps SELECT and INSERT only.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0048
Revises: v3_0047
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0048"
down_revision = "v3_0047"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

CREATE TABLE IF NOT EXISTS qa.compute_run (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  answer_id uuid NOT NULL,
  step_id text,
  runtime text NOT NULL,
  code_sha256 text NOT NULL,
  code_object_key text,
  input_refs jsonb NOT NULL,
  lib_versions jsonb NOT NULL DEFAULT '{}',
  output_sha256 text NOT NULL,
  output_rows integer NOT NULL,
  duration_ms integer NOT NULL,
  sandbox_runtime_class text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT compute_run_pkey PRIMARY KEY (id),
  CHECK (runtime IN ('insights','decomposition','pandas','duckdb')),
  CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (jsonb_typeof(input_refs) = 'array' AND jsonb_typeof(lib_versions) = 'object'),
  CHECK (output_rows >= 0 AND duration_ms >= 0),
  CHECK (sandbox_runtime_class IS NULL OR sandbox_runtime_class IN ('gvisor','runc'))
);
COMMENT ON TABLE qa.compute_run IS 'Một lần tính số dẫn xuất tất định của một câu trả lời (AL-01, F-09): mã đã chạy và phiên bản của nó, đầu vào (kết quả truy vấn, snapshot) kèm mã băm, đầu ra kèm mã băm, để replay tính lại và so (BR-177). Số dẫn xuất trong qa.answer.figures và qa.answer_receipt_figure (source_kind compute_run) trỏ tới dòng này. Chỉ ghi thêm. Thêm 23/11/2026. Use case: UC-01-07.';
COMMENT ON COLUMN qa.compute_run.answer_id IS 'FK answer';
COMMENT ON COLUMN qa.compute_run.step_id IS 'Nút PlanIR của bước đã tính; null với đường nhanh một chỉ số';
COMMENT ON COLUMN qa.compute_run.runtime IS 'insights, decomposition, pandas, duckdb';
COMMENT ON COLUMN qa.compute_run.code_sha256 IS 'SHA-256 của mã đã chạy (với insights: phiên bản `answer/insights.py`)';
COMMENT ON COLUMN qa.compute_run.code_object_key IS 'Khoá MinIO của mã sandbox (`sandbox-audit/<org>/<answer>/<step>`); null với mã trong repo';
COMMENT ON COLUMN qa.compute_run.input_refs IS 'Mảng {kind: query_run, snapshot, result_set; id; sha256}';
COMMENT ON COLUMN qa.compute_run.lib_versions IS 'Phiên bản thư viện tính (pandas, numpy, duckdb)';
COMMENT ON COLUMN qa.compute_run.output_sha256 IS 'SHA-256 của các số tính ra, JSON chuẩn hoá theo khoá';
COMMENT ON COLUMN qa.compute_run.output_rows IS 'Số số (dòng) tính ra';
COMMENT ON COLUMN qa.compute_run.sandbox_runtime_class IS 'gvisor, runc; null khi không chạy trong sandbox';
CREATE INDEX IF NOT EXISTS compute_run_answer_id_idx ON qa.compute_run (answer_id);
ALTER TABLE qa.compute_run DROP CONSTRAINT IF EXISTS compute_run_answer_id_fkey;
ALTER TABLE qa.compute_run ADD CONSTRAINT compute_run_answer_id_fkey FOREIGN KEY (answer_id) REFERENCES qa.answer(id);
ALTER TABLE qa.compute_run ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON qa.compute_run;
CREATE POLICY org_isolation ON qa.compute_run USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON qa.compute_run TO svc_qa;
GRANT SELECT ON qa.compute_run TO qa_read;

CREATE OR REPLACE FUNCTION qa.compute_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Lần tính chỉ ghi thêm, không sửa hay xoá (BR-177)' USING ERRCODE = 'check_violation';
END
$$;
DROP TRIGGER IF EXISTS compute_run_guard ON qa.compute_run;
CREATE TRIGGER compute_run_guard BEFORE UPDATE OR DELETE ON qa.compute_run
  FOR EACH ROW EXECUTE FUNCTION qa.compute_run_guard();
REVOKE UPDATE, DELETE ON qa.compute_run FROM svc_qa;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0048 is not reversed")
