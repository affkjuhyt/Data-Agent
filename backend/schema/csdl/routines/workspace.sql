-- routines/workspace.sql: what D4.11 (tab 3b) writes in words for schema workspace: the tile
-- position uniqueness, deferred so a reorder can swap positions inside one transaction.

-- covers: workspace.tile#0
ALTER TABLE workspace.tile ADD CONSTRAINT tile_workspace_id_position_key
  UNIQUE (workspace_id, position) DEFERRABLE INITIALLY DEFERRED;

-- The upload worker writes rows through a SECURITY DEFINER boundary so it never needs DDL
-- privileges on workspace. Explicit JSON element aliases avoid collisions with PL/pgSQL variables
-- when an uploaded header is named `value`.
GRANT USAGE ON SCHEMA workspace TO svc_query;
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
ALTER FUNCTION public.da_materialize_upload(text, jsonb, jsonb, boolean) OWNER TO da_owner;
GRANT EXECUTE ON FUNCTION public.da_materialize_upload(text, jsonb, jsonb, boolean) TO svc_ingest;
