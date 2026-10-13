"""What the A2 endpoints added on 12/10/2026 need from the database (tab 3b, decided 12/10/2026).

- agent.tool_def: the tool register (ADR-54, tab 5 G2.1), shared by every organisation, read by
  every service, written only here; GET /v1/tools reads it. Rows from backend/alembic/seed/tool_def.json.
- agent.agent_def may be locked (POST /v1/agents/{id}:lock); status_before_lock is what unlock restores.
- agent.agent_run.retry_of: the failed run a retry runs again (POST /v1/runs/{id}:retry).
- skill.skill_run.mode: a try from the skill editor (POST /v1/skill-versions/{id}:try) is not a run.
- query.query_run.caller_kind ad_hoc: SQL sent to POST /v1/query-runs.
- pipeline.data_mart.suggested_dimensions: dimensions proposed from the fact table's relations.
- pack.industry_pack.origin: an organisation's own pack (POST /v1/packs) has no publisher key yet.
- pack.metric_override.calibration_run_id: the trial run of an override (UC-09-03 step 3).
- svc_audit_worker in the audit database (tab 3 D5.2): worker-agent releases an approved export
  with it; it reads export_job and moves its state, nothing on audit_log, security_event, outbox.

Fresh installs get the schema from the generated dataagent.sql; this revision is idempotent so
both paths end equal, and both load the tool rows here.

Revision ID: v3_0011
Revises: v3_0010
"""

import json
import os
import time
import uuid
from pathlib import Path

import psycopg
import sqlalchemy as sa
from alembic import op

revision = "v3_0011"
down_revision = "v3_0010"
branch_labels = None
depends_on = None

SEED = Path(__file__).resolve().parents[1] / "seed" / "tool_def.json"

# Unnamed constraints of the baseline are found by their definition, not a guessed name.
DROP_CHECK = """
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = '{table}'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ '{pattern}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;
"""

SCHEMA = (
    "SET LOCAL ROLE da_owner;"
    # agent.tool_def
    """
CREATE TABLE IF NOT EXISTS agent.tool_def (
  id uuid NOT NULL,
  code text NOT NULL,
  permission_level text NOT NULL,
  owner_service text NOT NULL,
  endpoints text[] NOT NULL,
  model_description text NOT NULL,
  toolsets text[] NOT NULL DEFAULT '{}',
  execution_kinds text[] NOT NULL,
  restricted_agent_kinds text[] NOT NULL DEFAULT '{}',
  mcp_exposed boolean NOT NULL DEFAULT false,
  default_checkpoint text NOT NULL,
  use_cases text[] NOT NULL,
  schema_sha256 text,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tool_def_pkey PRIMARY KEY (id),
  UNIQUE (code),
  CHECK (code ~ '^[a-z]+(\\.[a-z_]+)+$'),
  CHECK (permission_level IN ('read','write_derived','send_external')),
  CHECK (default_checkpoint IN ('none','on_condition','before','always')),
  CHECK (status IN ('draft','active','deprecated')),
  CHECK (execution_kinds <@ ARRAY['interactive','plan','workflow'] AND cardinality(execution_kinds) > 0),
  CHECK (char_length(model_description) <= 400),
  CHECK (permission_level <> 'send_external' OR (default_checkpoint = 'always' AND NOT mcp_exposed)),
  CHECK (status <> 'active' OR cardinality(use_cases) > 0),
  CHECK (NOT mcp_exposed OR code !~ '^exec\\.')
);
CREATE INDEX IF NOT EXISTS tool_def_toolsets_idx ON agent.tool_def USING gin (toolsets);
CREATE INDEX IF NOT EXISTS tool_def_use_cases_idx ON agent.tool_def USING gin (use_cases);
COMMENT ON TABLE agent.tool_def IS 'Sổ công cụ của agent (tab 5 G2, G2.1); nạp bằng migration, không sửa qua API (thêm 12/10/2026)';
REVOKE INSERT, UPDATE, DELETE ON agent.tool_def FROM svc_agent;
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\\_%' LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA agent TO %I', r.rolname);
    EXECUTE format('GRANT SELECT ON agent.tool_def TO %I', r.rolname);
  END LOOP;
END $$;
"""
    # agent.agent_def: locked
    + """
ALTER TABLE agent.agent_def ADD COLUMN IF NOT EXISTS status_before_lock text;
COMMENT ON COLUMN agent.agent_def.status_before_lock IS 'Trạng thái trước khi khoá, mở khoá trả về trạng thái này; chỉ có giá trị khi locked (thêm 12/10/2026)';
"""
    + DROP_CHECK.format(table="agent.agent_def", pattern="status")
    + """
ALTER TABLE agent.agent_def
  ADD CONSTRAINT agent_def_status_check
    CHECK (status IN ('draft','testing','running','paused','retired','locked')),
  ADD CONSTRAINT agent_def_check
    CHECK ((status = 'locked') = (status_before_lock IS NOT NULL));
"""
    # agent.agent_run.retry_of
    + """
ALTER TABLE agent.agent_run ADD COLUMN IF NOT EXISTS retry_of uuid;
COMMENT ON COLUMN agent.agent_run.retry_of IS 'Lượt thất bại mà lượt này chạy lại với cùng đầu vào và phiên bản cấu hình (POST /v1/runs/{id}:retry, thêm 12/10/2026)';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_run_retry_of_fkey') THEN
    ALTER TABLE agent.agent_run ADD CONSTRAINT agent_run_retry_of_fkey
      FOREIGN KEY (retry_of) REFERENCES agent.agent_run(id);
  END IF;
END $$;
"""
    # skill.skill_run.mode
    + """
ALTER TABLE skill.skill_run ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'run';
COMMENT ON COLUMN skill.skill_run.mode IS 'run, try: try là chạy thử từ màn soạn kỹ năng (POST /v1/skill-versions/{id}:try), kết quả không dùng cho ô, cảnh báo hay agent (thêm 12/10/2026)';
"""
    + DROP_CHECK.format(table="skill.skill_run", pattern="mode = ")
    + """
ALTER TABLE skill.skill_run ADD CONSTRAINT skill_run_mode_check CHECK (mode IN ('run','try'));
"""
    # query.query_run.caller_kind: ad_hoc (a partitioned table: the CHECK goes to every partition)
    + DROP_CHECK.format(table="query.query_run", pattern="caller_kind")
    + """
ALTER TABLE query.query_run ADD CONSTRAINT query_run_caller_kind_check
  CHECK (caller_kind IN ('question','tile','skill_run','agent_run','pipeline','period','eval','receipt_replay','ad_hoc'));
COMMENT ON COLUMN query.query_run.caller_kind IS 'question, tile, skill_run, agent_run, pipeline, period, eval, receipt_replay (UC-01-07), ad_hoc (SQL người dùng gửi qua POST /v1/query-runs, thêm 12/10/2026)';
"""
    # pipeline.data_mart.suggested_dimensions
    + """
ALTER TABLE pipeline.data_mart ADD COLUMN IF NOT EXISTS suggested_dimensions jsonb;
COMMENT ON COLUMN pipeline.data_mart.suggested_dimensions IS 'Chiều gợi ý từ quan hệ của bảng sự kiện, sinh khi tạo mart; người soạn chọn vào dimensions (UC-06-06, thêm 12/10/2026)';
"""
    # pack.industry_pack.origin
    + """
ALTER TABLE pack.industry_pack ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'vendor';
ALTER TABLE pack.industry_pack ALTER COLUMN publisher_key_id DROP NOT NULL;
COMMENT ON COLUMN pack.industry_pack.origin IS 'vendor, organisation: organisation là gói tổ chức tự soạn qua POST /v1/packs; industry_code lấy theo organization.industry_code (thêm 12/10/2026)';
"""
    + DROP_CHECK.format(table="pack.industry_pack", pattern="origin")
    + """
ALTER TABLE pack.industry_pack
  ADD CONSTRAINT industry_pack_origin_check CHECK (origin IN ('vendor','organisation')),
  ADD CONSTRAINT industry_pack_check CHECK (origin = 'organisation' OR publisher_key_id IS NOT NULL);
"""
    # pack.metric_override.calibration_run_id
    + """
ALTER TABLE pack.metric_override ADD COLUMN IF NOT EXISTS calibration_run_id uuid;
COMMENT ON COLUMN pack.metric_override.calibration_run_id IS 'FK calibration_run; lần chạy thử công thức trên dữ liệu thật (UC-09-03 bước 3, thêm 12/10/2026)';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'metric_override_calibration_run_id_fkey') THEN
    ALTER TABLE pack.metric_override ADD CONSTRAINT metric_override_calibration_run_id_fkey
      FOREIGN KEY (calibration_run_id) REFERENCES pack.calibration_run(id);
  END IF;
END $$;
RESET ROLE;
"""
)

AUDIT_WORKER = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_audit_worker') THEN
    CREATE ROLE svc_audit_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
SET LOCAL ROLE da_owner;
GRANT USAGE ON SCHEMA audit TO svc_audit_worker;
GRANT SELECT ON audit.export_job TO svc_audit_worker;
GRANT UPDATE (status, approved_by, approved_at, updated_at) ON audit.export_job TO svc_audit_worker;
DROP POLICY IF EXISTS worker_decision_only ON audit.export_job;
CREATE POLICY worker_decision_only ON audit.export_job AS RESTRICTIVE FOR UPDATE TO svc_audit_worker
  USING (status = 'waiting_approval')
  WITH CHECK ((status = 'queued' AND approved_by IS NOT NULL AND approved_at IS NOT NULL) OR status = 'failed');
RESET ROLE;
"""

UPSERT_TOOL = """
INSERT INTO agent.tool_def (id, code, permission_level, owner_service, endpoints, model_description,
  toolsets, execution_kinds, restricted_agent_kinds, mcp_exposed, default_checkpoint, use_cases, status)
VALUES (%(id)s, %(code)s, %(permission_level)s, %(owner_service)s, %(endpoints)s,
  %(model_description)s, %(toolsets)s, %(execution_kinds)s, %(restricted_agent_kinds)s,
  %(mcp_exposed)s, %(default_checkpoint)s, %(use_cases)s, %(status)s)
ON CONFLICT (code) DO UPDATE SET
  permission_level = EXCLUDED.permission_level, owner_service = EXCLUDED.owner_service,
  endpoints = EXCLUDED.endpoints, model_description = EXCLUDED.model_description,
  toolsets = EXCLUDED.toolsets, execution_kinds = EXCLUDED.execution_kinds,
  restricted_agent_kinds = EXCLUDED.restricted_agent_kinds, mcp_exposed = EXCLUDED.mcp_exposed,
  default_checkpoint = EXCLUDED.default_checkpoint, use_cases = EXCLUDED.use_cases,
  status = EXCLUDED.status, updated_at = now()
"""


def _uuid7() -> uuid.UUID:
    # Ids are uuid7 everywhere (dataagent_db.base); the migrate image does not ship libs.
    value = (time.time_ns() // 1_000_000) << 80 | int.from_bytes(os.urandom(10), "big")
    value = value & ~(0xF << 76) | 0x7 << 76
    value = value & ~(0x3 << 62) | 0x2 << 62
    return uuid.UUID(int=value)


def upgrade() -> None:
    # op.execute wraps a string in text(), where ":retry" in a comment reads as a bind parameter.
    op.execute(sa.text(SCHEMA.replace(":", "\\:")))
    bind = op.get_bind()
    defaults = {
        "toolsets": [],
        "restricted_agent_kinds": [],
        "mcp_exposed": False,
        "status": "active",
    }
    for tool in json.loads(SEED.read_text(encoding="utf-8")):
        bind.exec_driver_sql(UPSERT_TOOL, {**defaults, **tool, "id": _uuid7()})
    url = bind.engine.url
    server = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    with psycopg.connect(**server, dbname=f"{url.database}_audit") as audit:
        audit.execute(AUDIT_WORKER)


def downgrade() -> None:
    raise NotImplementedError("v3_0011 is not reversed: it would drop columns that hold data")
