"""v2_schemas_and_service_roles

Foundation for the V2 data platform (NOP-147): the five bounded-context schemas,
one NOBYPASSRLS login role per service, and btree_gist for the non-overlapping
validity ranges later tables need. No business tables here: each table arrives
in the migration of the story that owns it and must call the RLS helper of
NOP-148 there.

Layout and ownership: docs/v2/10-database-design.md section 4 and
docs/v2/14-input-data-db.md section 9.3. Why the crawler has its own role:
ADR-V2-46 (web_crawler fetches operator-supplied URLs from the Internet, so if it
is compromised it must not be able to write candidates, mappings or the catalog).

Grants are deliberately two-level:
  * each owner role gets SELECT/INSERT/UPDATE/DELETE on future tables of its own
    schema by default privilege, so a new table does not silently deny its
    owner; append-only tables revoke UPDATE/DELETE in their own migration;
  * other roles only get USAGE on the schemas they read. USAGE alone exposes
    nothing: every cross-schema read is granted per table by the migration that
    creates the table.

Passwords are set out-of-band by the db-init Job from the vault, the same
convention as app_user (a1b2c3d4e5f6) and kb_structured_reader (e1f2a3b4c5d6).

Revision ID: 5b7d9f1a3c6e
Revises: b1d3f5a7c9e2
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b7d9f1a3c6e"
down_revision: str | None = "b1d3f5a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("registry", "ingest", "governance", "knowledge", "alert")

# role -> (schema it owns and writes, or None; schemas it may read)
ROLES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "registry_svc": ("registry", ("ingest",)),
    "ingest_svc": ("ingest", ("registry", "knowledge")),
    "crawler_svc": (None, ("ingest",)),
    "governance_svc": ("governance", ("ingest", "registry", "knowledge")),
    "knowledge_svc": ("knowledge", ("registry",)),
    "alert_svc": ("alert", ("registry", "governance")),
    "outbox_relay": (None, ("ingest",)),
    "maintenance": (None, SCHEMAS),
}

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _create_role(role: str) -> str:
    # Every attribute stated rather than relied on as a default: one BYPASSRLS
    # anywhere silently disables every policy for that role.
    return f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE {role} LOGIN;
    END IF;
    ALTER ROLE {role} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO {role}', current_database());
END
$$;
"""


def _revoke_connect(role: str) -> str:
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {role}', current_database());
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC")

    # asyncpg rejects several statements in one prepared call, so one execute each.
    for role, (owned, reads) in ROLES.items():
        op.execute(_create_role(role))
        for schema in {s for s in (owned, *reads) if s}:
            op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        if owned:
            op.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {owned} "
                f"GRANT {_TABLE_PRIVILEGES} ON TABLES TO {role}"
            )
            op.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {owned} "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {role}"
            )


def downgrade() -> None:
    for role, (owned, reads) in ROLES.items():
        if owned:
            op.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {owned} "
                f"REVOKE {_TABLE_PRIVILEGES} ON TABLES FROM {role}"
            )
            op.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {owned} "
                f"REVOKE USAGE, SELECT ON SEQUENCES FROM {role}"
            )
        for schema in {s for s in (owned, *reads) if s}:
            op.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM {role}")
        op.execute(_revoke_connect(role))
        op.execute(f"DROP ROLE IF EXISTS {role}")

    # RESTRICT (the default): a schema that still holds tables means a later V2
    # migration has not been downgraded first, and that must fail loudly.
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")

    op.execute("DROP EXTENSION IF EXISTS btree_gist")
