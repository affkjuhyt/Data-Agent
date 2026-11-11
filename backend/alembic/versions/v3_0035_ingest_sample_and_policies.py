"""Ingest job sample (dry-run poll) and run-policy columns for JobCreate (poll-CDC epic).

Revision ID: v3_0035
Revises: v3_0034
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0035"
down_revision = "v3_0034"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;

CREATE TABLE IF NOT EXISTS ingest.job_sample (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL,
  data_space_id uuid NOT NULL,
  source_id uuid NOT NULL,
  principal_id uuid NOT NULL,
  name text NOT NULL,
  mode text NOT NULL,
  source_object text NOT NULL,
  cursor_column text,
  status text NOT NULL DEFAULT 'running',
  sample jsonb NOT NULL DEFAULT '[]',
  errors jsonb NOT NULL DEFAULT '[]',
  note text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CONSTRAINT job_sample_status_check CHECK (status IN ('running','succeeded','failed')),
  CONSTRAINT job_sample_mode_check CHECK (mode IN ('full','incremental','cdc'))
);
ALTER TABLE ingest.job_sample ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'ingest' AND tablename = 'job_sample'
      AND policyname = 'org_isolation'
  ) THEN
    CREATE POLICY org_isolation ON ingest.job_sample
      USING (org_id = (NULLIF(current_setting('app.org_id', true), ''))::uuid)
      WITH CHECK (org_id = (NULLIF(current_setting('app.org_id', true), ''))::uuid);
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE ON ingest.job_sample TO svc_ingest;
GRANT SELECT, INSERT, UPDATE ON ingest.job_sample TO svc_api;

ALTER TABLE ingest.ingestion_job
  ADD COLUMN IF NOT EXISTS retry_policy text NOT NULL DEFAULT 'backoff_3',
  ADD COLUMN IF NOT EXISTS on_schema_change text NOT NULL DEFAULT 'accept_new_columns',
  ADD COLUMN IF NOT EXISTS on_invalid_record text NOT NULL DEFAULT 'quarantine_30d',
  ADD COLUMN IF NOT EXISTS skip_today boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS merge_key_columns text[];
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ingestion_job_retry_policy_check'
      AND conrelid = 'ingest.ingestion_job'::regclass
  ) THEN
    ALTER TABLE ingest.ingestion_job ADD CONSTRAINT ingestion_job_retry_policy_check
      CHECK (retry_policy IN ('backoff_3','once','none'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ingestion_job_on_schema_change_check'
      AND conrelid = 'ingest.ingestion_job'::regclass
  ) THEN
    ALTER TABLE ingest.ingestion_job ADD CONSTRAINT ingestion_job_on_schema_change_check
      CHECK (on_schema_change IN ('accept_new_columns','pause_and_ask','continue_and_notify'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ingestion_job_on_invalid_record_check'
      AND conrelid = 'ingest.ingestion_job'::regclass
  ) THEN
    ALTER TABLE ingest.ingestion_job ADD CONSTRAINT ingestion_job_on_invalid_record_check
      CHECK (on_invalid_record IN ('quarantine_30d','skip_and_log','stop'));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.da_materialize_upsert(
  p_table_name text,
  p_columns jsonb,
  p_rows jsonb,
  p_keys text[]
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
  column_def text := '';
  column_names text := '';
  key_list text := '';
  set_list text := '';
  values_sql text;
  item jsonb;
  value jsonb;
  row_index integer;
  first_column boolean := true;
  key text;
  col_name text;
BEGIN
  IF p_table_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid upload table name';
  END IF;
  IF p_keys IS NULL OR array_length(p_keys, 1) IS NULL THEN
    RAISE EXCEPTION 'merge keys required';
  END IF;
  FOR item IN SELECT column_item FROM jsonb_array_elements(p_columns) AS elements(column_item)
  LOOP
    IF NOT first_column THEN
      column_def := column_def || ', ';
      column_names := column_names || ', ';
    END IF;
    first_column := false;
    column_def := column_def || format('%I %s', item->>'name', item->>'type');
    column_names := column_names || format('%I', item->>'name');
  END LOOP;
  IF to_regclass(format('workspace.%I', p_table_name)) IS NULL THEN
    EXECUTE format('CREATE TABLE workspace.%I (%s)', p_table_name, column_def);
    EXECUTE format('GRANT SELECT ON TABLE workspace.%I TO svc_query', p_table_name);
  END IF;
  key_list := '';
  FOREACH key IN ARRAY p_keys LOOP
    IF key_list <> '' THEN key_list := key_list || ', '; END IF;
    key_list := key_list || format('%I', key);
  END LOOP;
  EXECUTE format(
    'CREATE UNIQUE INDEX IF NOT EXISTS %I ON workspace.%I (%s)',
    'ux_' || p_table_name || '_merge', p_table_name, key_list
  );
  FOR item IN SELECT row_item FROM jsonb_array_elements(p_rows) AS elements(row_item)
  LOOP
    values_sql := '';
    row_index := 0;
    FOR value IN SELECT row_value FROM jsonb_array_elements(item) AS elements(row_value)
    LOOP
      IF row_index > 0 THEN values_sql := values_sql || ', '; END IF;
      values_sql := values_sql || CASE
        WHEN value IS NULL OR value = 'null'::jsonb OR value #>> '{}' = '' THEN 'NULL'
        ELSE quote_nullable(value #>> '{}')
      END;
      row_index := row_index + 1;
    END LOOP;
    set_list := '';
    FOR item IN SELECT column_item FROM jsonb_array_elements(p_columns) AS elements(column_item)
    LOOP
      col_name := item->>'name';
      IF col_name = ANY (p_keys) THEN CONTINUE; END IF;
      IF set_list <> '' THEN set_list := set_list || ', '; END IF;
      set_list := set_list || format('%I = EXCLUDED.%I', col_name, col_name);
    END LOOP;
    IF set_list = '' THEN
      EXECUTE format(
        'INSERT INTO workspace.%I (%s) VALUES (%s) ON CONFLICT (%s) DO NOTHING',
        p_table_name, column_names, values_sql, key_list
      );
    ELSE
      EXECUTE format(
        'INSERT INTO workspace.%I (%s) VALUES (%s) ON CONFLICT (%s) DO UPDATE SET %s',
        p_table_name, column_names, values_sql, key_list, set_list
      );
    END IF;
  END LOOP;
END
$fn$;
ALTER FUNCTION public.da_materialize_upsert(text, jsonb, jsonb, text[]) OWNER TO da_owner;
GRANT EXECUTE ON FUNCTION public.da_materialize_upsert(text, jsonb, jsonb, text[]) TO svc_ingest;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0035 is not reversed")
