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
ALTER FUNCTION public.da_materialize_upload(text, jsonb, jsonb, boolean) OWNER TO da_owner;
GRANT EXECUTE ON FUNCTION public.da_materialize_upload(text, jsonb, jsonb, boolean) TO svc_ingest;

-- RES-17 (v3_0035): incremental and CDC jobs upsert on the job's merge_key_columns instead of
-- appending, through the same SECURITY DEFINER boundary as da_materialize_upload. The function
-- runs as da_owner, so it checks its input as da_materialize_upload does (v3_0036) rather than
-- trusting its caller: a column type is spliced into DDL, a name or key into identifiers.
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
-- DF-02 (F-01): a full ingest run writes a new table workspace.<name>__v<n> and this points the
-- view workspace.<name>, which every reader names, at it, in the run's transaction: a run that
-- fails leaves the view and the version it shows as they were, and no version is dropped. A table
-- still standing where the view goes (written before versioning) becomes the version it was,
-- renamed to p_legacy, never dropped. An incremental run calls it on the current version too, to
-- show the columns it added. Names are checked as da_materialize_upload checks them; a version's
-- name is its base then __v<n>: the name itself when it has 54 characters or fewer, else its first
-- 38, `_` and 16 hex of the sha256 of the whole name, always 55, so a plain base never equals a
-- hashed one (libs/dataagent_db/versioning.version_base). Not the name cut to 55, which dropped the job
-- id that makes an ingest table's name unique, so two jobs of two organisations could share
-- version tables in the one workspace schema. The workspace schema is shared by every
-- organisation, so the caller's organisation must be set and no other organisation's dataset may
-- hold the name, and a name shaped like a version table is refused: its view would stand on
-- another dataset's version.
CREATE OR REPLACE FUNCTION public.da_swap_version(
  p_name text,
  p_physical text,
  p_legacy text DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
  kind "char";
  org uuid;
  base text;
  version_name text;
  renamed boolean := false;
BEGIN
  IF p_name !~ '^[a-z][a-z0-9_]{0,62}$' OR p_name ~ '__v[0-9]+$' THEN
    RAISE EXCEPTION 'invalid workspace name';
  END IF;
  org := NULLIF(current_setting('app.org_id', true), '')::uuid;
  IF org IS NULL THEN
    RAISE EXCEPTION 'no organisation set for this session';
  END IF;
  IF EXISTS (SELECT 1 FROM catalog.dataset d WHERE d.schema_name = 'workspace'
             AND d.table_name = p_name AND d.org_id <> org) THEN
    RAISE EXCEPTION 'workspace.% belongs to another organisation', p_name;
  END IF;
  base := CASE WHEN length(p_name) <= 54 THEN p_name
               ELSE left(p_name, 38) || '_' || left(encode(sha256(convert_to(p_name, 'UTF8')), 'hex'), 16)
          END;
  -- base is [a-z0-9_] only, so it is safe inside the pattern.
  version_name := '^' || base || '__v[0-9]{1,5}$';
  IF p_physical !~ version_name THEN
    RAISE EXCEPTION 'invalid version table name';
  END IF;
  IF to_regclass(format('workspace.%I', p_physical)) IS NULL THEN
    RAISE EXCEPTION 'no version table %', p_physical;
  END IF;
  SELECT c.relkind INTO kind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'workspace' AND c.relname = p_name;
  IF kind = 'r' THEN
    IF p_legacy IS NULL OR p_legacy !~ version_name OR p_legacy = p_physical THEN
      RAISE EXCEPTION 'a table stands where the view goes: name the version it becomes';
    END IF;
    EXECUTE format('ALTER TABLE workspace.%I RENAME TO %I', p_name, p_legacy);
    renamed := true;
  ELSIF kind IS NOT NULL AND kind <> 'v' THEN
    RAISE EXCEPTION 'workspace.% is neither a table nor a view', p_name;
  END IF;
  BEGIN
    EXECUTE format('CREATE OR REPLACE VIEW workspace.%I AS SELECT * FROM workspace.%I', p_name, p_physical);
  EXCEPTION WHEN invalid_table_definition THEN
    -- A version that lost or retyped a column cannot replace the view in place. Dropping it fails,
    -- and the run with it, when something depends on the view: nothing is dropped behind a reader.
    EXECUTE format('DROP VIEW workspace.%I', p_name);
    EXECUTE format('CREATE VIEW workspace.%I AS SELECT * FROM workspace.%I', p_name, p_physical);
  END;
  EXECUTE format('GRANT SELECT ON TABLE workspace.%I TO svc_query', p_name);
  RETURN renamed;
END
$fn$;
ALTER FUNCTION public.da_swap_version(text, text, text) OWNER TO da_owner;
GRANT EXECUTE ON FUNCTION public.da_swap_version(text, text, text) TO svc_ingest;
