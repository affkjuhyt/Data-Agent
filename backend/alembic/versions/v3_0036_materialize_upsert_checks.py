"""da_materialize_upsert checks its input as da_materialize_upload does.

v3_0035's upsert runs as da_owner (SECURITY DEFINER) and spliced each column's type into CREATE
TABLE and its name and the merge keys into identifiers without checking them: it relied on
worker-ingest sending only _WORKSPACE_TYPES and physical names. It now refuses, as
da_materialize_upload does, a column name outside [a-z][a-z0-9_]{0,62}, a type other than text,
numeric, boolean or date, no columns or more than 2,000, and a merge key that is not one of the
columns (before, that failed later, on the unique index). The columns an upsert overwrites are
built once instead of per row.

Fresh installs get the same from dataagent.sql (routines/workspace.sql); CREATE OR REPLACE, so
this revision applies over either.

Revision ID: v3_0036
Revises: v3_0035
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0036"
down_revision = "v3_0035"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

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
  row_item jsonb;
  value jsonb;
  row_index integer;
  first_column boolean := true;
  names text[] := '{}';
  key text;
BEGIN
  IF p_table_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid upload table name';
  END IF;
  IF jsonb_array_length(p_columns) = 0 OR jsonb_array_length(p_columns) > 2000 THEN
    RAISE EXCEPTION 'invalid upload columns';
  END IF;
  IF p_keys IS NULL OR array_length(p_keys, 1) IS NULL THEN
    RAISE EXCEPTION 'merge keys required';
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
    names := names || (item->>'name');
  END LOOP;
  FOREACH key IN ARRAY p_keys LOOP
    IF key IS NULL OR NOT key = ANY (names) THEN
      RAISE EXCEPTION 'merge key % is not a column of the rows', key;
    END IF;
    IF key_list <> '' THEN key_list := key_list || ', '; END IF;
    key_list := key_list || format('%I', key);
  END LOOP;
  -- The columns an upsert overwrites: every one but the keys, the same for every row.
  FOR item IN SELECT column_item FROM jsonb_array_elements(p_columns) AS elements(column_item)
  LOOP
    IF (item->>'name') = ANY (p_keys) THEN CONTINUE; END IF;
    IF set_list <> '' THEN set_list := set_list || ', '; END IF;
    set_list := set_list || format('%I = EXCLUDED.%I', item->>'name', item->>'name');
  END LOOP;
  IF to_regclass(format('workspace.%I', p_table_name)) IS NULL THEN
    EXECUTE format('CREATE TABLE workspace.%I (%s)', p_table_name, column_def);
    EXECUTE format('GRANT SELECT ON TABLE workspace.%I TO svc_query', p_table_name);
  END IF;
  EXECUTE format(
    'CREATE UNIQUE INDEX IF NOT EXISTS %I ON workspace.%I (%s)',
    'ux_' || p_table_name || '_merge', p_table_name, key_list
  );
  FOR row_item IN SELECT element FROM jsonb_array_elements(p_rows) AS elements(element)
  LOOP
    values_sql := '';
    row_index := 0;
    FOR value IN SELECT row_value FROM jsonb_array_elements(row_item) AS elements(row_value)
    LOOP
      IF row_index > 0 THEN values_sql := values_sql || ', '; END IF;
      values_sql := values_sql || CASE
        WHEN value IS NULL OR value = 'null'::jsonb OR value #>> '{}' = '' THEN 'NULL'
        ELSE quote_nullable(value #>> '{}')
      END;
      row_index := row_index + 1;
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
    raise NotImplementedError("v3_0036 is not reversed")
