"""An append into a workspace table adds the columns its source gained (RES-18, after Airbyte's
schema evolution): before, the INSERT failed on the unknown column. A column the source dropped stays
and is NULL from then on; a retyped one is refused by worker-ingest as schema drift.

Fresh installs get the function from the generated dataagent.sql; CREATE OR REPLACE makes this
revision idempotent so both paths end equal.

Revision ID: v3_0030
Revises: v3_0029
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0030"
down_revision = "v3_0029"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;
CREATE OR REPLACE FUNCTION public.da_materialize_upload(
  p_table_name text,
  p_columns jsonb,
  p_rows jsonb,
  p_replace boolean DEFAULT false
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
  column_def text := '';
  column_names text := '';
  values_sql text;
  item jsonb;
  value jsonb;
  row_index integer;
  first_column boolean := true;
BEGIN
  IF p_table_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid upload table name';
  END IF;
  IF jsonb_array_length(p_columns) = 0 OR jsonb_array_length(p_columns) > 2000 THEN
    RAISE EXCEPTION 'invalid upload columns';
  END IF;
  FOR item IN SELECT column_item FROM jsonb_array_elements(p_columns) AS elements(column_item)
  LOOP
    IF (item->>'name') !~ '^[a-z][a-z0-9_]{0,62}$' THEN
      RAISE EXCEPTION 'invalid upload column name';
    END IF;
    IF (item->>'type') NOT IN ('text', 'numeric', 'boolean', 'date') THEN
      RAISE EXCEPTION 'invalid upload column type';
    END IF;
    IF NOT first_column THEN
      column_def := column_def || ', ';
      column_names := column_names || ', ';
    END IF;
    first_column := false;
    column_def := column_def || format('%I %s', item->>'name', item->>'type');
    column_names := column_names || format('%I', item->>'name');
  END LOOP;
  IF p_replace OR to_regclass(format('workspace.%I', p_table_name)) IS NULL THEN
    EXECUTE format('DROP TABLE IF EXISTS workspace.%I', p_table_name);
    EXECUTE format('CREATE TABLE workspace.%I (%s)', p_table_name, column_def);
    EXECUTE format('GRANT SELECT ON TABLE workspace.%I TO svc_query', p_table_name);
  ELSE
    -- RES-18: an append whose source gained a column adds it (earlier rows hold NULL); a column
    -- the source dropped stays and is NULL from now on. A retyped column is refused by the caller.
    FOR item IN SELECT column_item FROM jsonb_array_elements(p_columns) AS elements(column_item)
    LOOP
      IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass(format('workspace.%I', p_table_name))
          AND attname = item->>'name' AND attnum > 0 AND NOT attisdropped
      ) THEN
        EXECUTE format('ALTER TABLE workspace.%I ADD COLUMN %I %s',
                       p_table_name, item->>'name', item->>'type');
      END IF;
    END LOOP;
  END IF;
  row_index := 0;
  FOR item IN SELECT row_item FROM jsonb_array_elements(p_rows) AS elements(row_item)
  LOOP
    values_sql := '';
    FOR value IN SELECT row_value FROM jsonb_array_elements(item) AS elements(row_value)
    LOOP
      IF row_index > 0 THEN
        values_sql := values_sql || ', ';
      END IF;
      values_sql := values_sql || CASE
        WHEN value IS NULL OR value = 'null'::jsonb OR value #>> '{}' = '' THEN 'NULL'
        ELSE quote_nullable(value #>> '{}')
      END;
      row_index := row_index + 1;
    END LOOP;
    EXECUTE format('INSERT INTO workspace.%I (%s) VALUES (%s)', p_table_name, column_names, values_sql);
    row_index := 0;
  END LOOP;
END
$fn$;
RESET ROLE;
"""


def upgrade() -> None:
    # op.execute wraps a string in text(), where a colon reads as a bind parameter.
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0030 is not reversed")
