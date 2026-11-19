"""A version table's name keeps what makes its dataset's name unique (security fix of DF-02).

v3_0041's da_swap_version accepted `<name cut to 55>__v<n>`: an ingest table is
`ingest_<stem, 45>_<job id, 8 hex>`, so the cut dropped most of the job id and two long-named
jobs, of two organisations as well, could share version tables in the one workspace schema
(security review of 5d59b877). The base is now the name itself when it fits in 55 characters,
else its first 46, `_` and 8 hex of the sha256 of the whole name, derived here and in
worker_ingest/versioning.version_base alike. No version table existed on the dev cluster yet.

Revision ID: v3_0042
Revises: v3_0041
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0042"
down_revision = "v3_0041"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

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
  base text;
  version_name text;
  renamed boolean := false;
BEGIN
  IF p_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'invalid workspace name';
  END IF;
  base := CASE WHEN length(p_name) <= 55 THEN p_name
               ELSE left(p_name, 46) || '_' || left(encode(sha256(convert_to(p_name, 'UTF8')), 'hex'), 8)
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

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0042 is not reversed")
