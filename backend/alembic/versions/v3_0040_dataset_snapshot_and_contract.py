"""A dataset's versions keep their physical table and Parquet snapshot; a dataset has a contract (DF-01).

The data foundation (docs/blueprint/DATA_PIPELINE_SPEC.md §2, API_AND_SCHEMA_CONTRACTS.md §2.1–2.3):

- catalog.dataset_version.physical_table, retained_until: the table workspace.<name>__v<n> of each
  version (DF-02 builds it instead of replacing the one table) and how long it is kept;
- catalog.dataset_snapshot: the Parquet copy of a version (DF-04 writes it), append-only, so an
  answer and its receipt read their numbers again once the version's table is gone;
- catalog.dataset_contract: the schema, freshness and rules a steward commits a dataset to (DF-07
  to DF-09 read and edit it).

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0040
Revises: v3_0039
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0040"
down_revision = "v3_0039"
branch_labels = None
depends_on = None

ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"

SQL = f"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.dataset_version
  ADD COLUMN IF NOT EXISTS physical_table text,
  ADD COLUMN IF NOT EXISTS retained_until timestamptz;

CREATE TABLE IF NOT EXISTS catalog.dataset_snapshot (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  version integer NOT NULL,
  status text NOT NULL DEFAULT 'ok',
  object_key text,
  sha256 text,
  row_count bigint NOT NULL,
  byte_size bigint,
  columns jsonb NOT NULL,
  available_at timestamptz NOT NULL,
  created_by_run_id uuid,
  created_by_kind text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT dataset_snapshot_pkey PRIMARY KEY (id),
  UNIQUE (dataset_id, version),
  CHECK (status IN ('ok','skipped')),
  CHECK (created_by_kind IN ('job_run','pipeline_run','upload','derive')),
  CHECK (status = 'skipped' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND byte_size IS NOT NULL)),
  CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{{64}}$'),
  CHECK (row_count >= 0),
  CONSTRAINT dataset_snapshot_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES catalog.dataset(id)
);
CREATE INDEX IF NOT EXISTS dataset_snapshot_dataset_id_version_idx
  ON catalog.dataset_snapshot (dataset_id, version DESC);
ALTER TABLE catalog.dataset_snapshot ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON catalog.dataset_snapshot;
CREATE POLICY org_isolation ON catalog.dataset_snapshot USING (org_id = {ORG}) WITH CHECK (org_id = {ORG});
-- Append-only: the owning service inserts and reads, never updates or deletes.
GRANT SELECT, INSERT ON catalog.dataset_snapshot TO svc_catalog;
GRANT SELECT ON catalog.dataset_snapshot TO catalog_read;

CREATE TABLE IF NOT EXISTS catalog.dataset_contract (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  version integer NOT NULL DEFAULT 1,
  schema_hash_expected text,
  freshness_sla jsonb NOT NULL DEFAULT '{{}}',
  required_rules jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'draft',
  broken_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid,
  CONSTRAINT dataset_contract_pkey PRIMARY KEY (id),
  UNIQUE (dataset_id),
  CHECK (status IN ('draft','active','broken')),
  CHECK (status <> 'broken' OR (broken_reason IS NOT NULL AND btrim(broken_reason) <> '')),
  CHECK (version >= 1),
  CHECK (jsonb_typeof(freshness_sla) = 'object'),
  CHECK (jsonb_typeof(required_rules) = 'array'),
  CONSTRAINT dataset_contract_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES catalog.dataset(id)
);
ALTER TABLE catalog.dataset_contract ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON catalog.dataset_contract;
CREATE POLICY org_isolation ON catalog.dataset_contract USING (org_id = {ORG}) WITH CHECK (org_id = {ORG});
GRANT SELECT, INSERT, UPDATE, DELETE ON catalog.dataset_contract TO svc_catalog;
GRANT SELECT ON catalog.dataset_contract TO catalog_read;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0040 is not reversed")
