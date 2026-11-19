"""A full ingest run builds a new version table and points the view at it (DF-02).

public.da_swap_version (routines/workspace.sql, copied here as it stands on 19/11/2026) points the
view workspace.<name> at workspace.<name>__v<n> in the run's transaction; a table still standing
where the view goes becomes the version it was, renamed, never dropped.
catalog.dataset_version.change_kind gains `reload`: a full run's new version, which may change the
schema without the proposal a `breaking` change carries.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0041
Revises: v3_0040
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0041"
down_revision = "v3_0040"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.dataset_version DROP CONSTRAINT IF EXISTS dataset_version_change_kind_check;
ALTER TABLE catalog.dataset_version ADD CONSTRAINT dataset_version_change_kind_check
  CHECK (change_kind IN ('initial','additive','breaking','reload'));

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
  version_name text;
  renamed boolean := false;
BEGIN
  IF p_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid workspace name';
  END IF;
  -- p_name is [a-z0-9_] only, so it is safe inside the pattern.
  version_name := '^' || left(p_name, 55) || '__v[0-9]{1,5}$';
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

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0041 is not reversed")
