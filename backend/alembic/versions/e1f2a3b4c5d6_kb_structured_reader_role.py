"""kb_structured read-only role for the text-to-SQL agent tool

Part of "Track B" from the KB stress-test session: the structured-table
feature (propose/review/approve an LLM-proposed schema -> a real
`kb_structured.<table>` Postgres table) has existed since earlier this
session, but nothing could ever query those tables — the chat agent's
DB Connector tool needs its own Postgres role to point at, and it must NOT
be the app's own `admin`/`app_user` role: those can see every internal
CamelCase table (KBData, Users, ...) in the `public` schema, which would be
a cross-tenant data leak the moment a SQL-generation prompt got a table list
back. `kb_structured_reader` is SELECT-only and scoped to just the
`kb_structured` schema via GRANT (nothing broader is ever granted, so
`public` stays invisible to it by Postgres's normal deny-by-default) and via
a default `search_path` (so unqualified table names in agent-generated SQL —
"SELECT * FROM foo" rather than "kb_structured.foo" — resolve correctly
without requiring any change to how that SQL gets written).

ALTER DEFAULT PRIVILEGES here is scoped to whichever role creates the table
(`FOR ROLE app_user` — the role kb-backend's structured_table.py router
actually runs `CREATE TABLE kb_structured.*` as) so tables created by future
approvals are automatically readable without a fixup GRANT per table; the
approve endpoint also GRANTs explicitly as a belt-and-braces measure since
which role actually opened that connection can vary by environment.

Password set out-of-band (vault, k8s secret), same convention as app_user in
a1b2c3d4e5f6_enable_tenant_rls.py — this migration intentionally does not
embed credentials.

Revision ID: e1f2a3b4c5d6
Revises: c8d9e0f1a2b3
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CREATE_ROLE = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_structured_reader') THEN
        -- Password set out-of-band (vault, k8s secret) — see module docstring.
        CREATE ROLE kb_structured_reader LOGIN NOBYPASSRLS;
    END IF;
    ALTER ROLE kb_structured_reader SET search_path = kb_structured;
END
$$;
"""

# asyncpg (this migration's driver) rejects multiple statements per prepared
# call, so each GRANT is its own op.execute — same constraint noted in
# a1b2c3d4e5f6_enable_tenant_rls.py.
_GRANT_USAGE_SCHEMA = "GRANT USAGE ON SCHEMA kb_structured TO kb_structured_reader"
_GRANT_SELECT_TABLES = "GRANT SELECT ON ALL TABLES IN SCHEMA kb_structured TO kb_structured_reader"
_GRANT_DEFAULT_TABLES = (
    "ALTER DEFAULT PRIVILEGES FOR ROLE app_user IN SCHEMA kb_structured "
    "GRANT SELECT ON TABLES TO kb_structured_reader"
)

_REVOKE_DEFAULT_TABLES = (
    "ALTER DEFAULT PRIVILEGES FOR ROLE app_user IN SCHEMA kb_structured "
    "REVOKE SELECT ON TABLES FROM kb_structured_reader"
)
_REVOKE_SELECT_TABLES = "REVOKE ALL ON ALL TABLES IN SCHEMA kb_structured FROM kb_structured_reader"
_REVOKE_USAGE_SCHEMA = "REVOKE USAGE ON SCHEMA kb_structured FROM kb_structured_reader"


def upgrade() -> None:
    op.execute(_CREATE_ROLE)
    op.execute(_GRANT_USAGE_SCHEMA)
    op.execute(_GRANT_SELECT_TABLES)
    op.execute(_GRANT_DEFAULT_TABLES)


def downgrade() -> None:
    op.execute(_REVOKE_DEFAULT_TABLES)
    op.execute(_REVOKE_SELECT_TABLES)
    op.execute(_REVOKE_USAGE_SCHEMA)
