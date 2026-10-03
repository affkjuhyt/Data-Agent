"""rls_kb_graph_tables

Row-level security for the four KB tables that have a tenant_id and none
(10-database-design §2.2):

  * "KBKnowledgeBase", "KBNeo4jNode", "KBNeo4jRelationship": tenant_isolation.
  * "KBEntityLookup": its aliases are per tenant or global (tenant_id NULL;
    entity_registry resolves a tenant row before a global one). A tenant reads
    its own rows and the global ones and writes only its own.

kb-backend reads them under the request's tenant; the KB workers' GraphStore
and entity_registry calls now pass the tenant they were given.

Revision ID: 6c1f4a8e3b5d
Revises: 5b9e3d7a2c4f
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6c1f4a8e3b5d"
down_revision: str | None = "5b9e3d7a2c4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWN = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# table -> (USING, WITH CHECK)
_POLICIES: dict[str, tuple[str, str]] = {
    "KBKnowledgeBase": (_OWN, _OWN),
    "KBNeo4jNode": (_OWN, _OWN),
    "KBNeo4jRelationship": (_OWN, _OWN),
    "KBEntityLookup": (f"tenant_id IS NULL OR {_OWN}", _OWN),
}
_POLICY = "tenant_isolation"


def upgrade() -> None:
    for table, (using, check) in _POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(
            f'CREATE POLICY {_POLICY} ON "{table}" AS PERMISSIVE FOR ALL TO PUBLIC '
            f"USING ({using}) WITH CHECK ({check})"
        )


def downgrade() -> None:
    for table in _POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
