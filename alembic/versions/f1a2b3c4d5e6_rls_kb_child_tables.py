"""enable tenant RLS on KB child tables that lack a tenant_id column

Revision ID: f1a2b3c4d5e6
Revises: 57abfbeabb56
Create Date: 2026-07-11

The original RLS migration (a1b2c3d4e5f6) enabled row-level security on every
public table that HAS a `tenant_id` column. Several KB child tables carry no
`tenant_id` of their own — they inherit tenancy through a foreign key to a
tenant-scoped parent:

    KBTextBlock         → KBData             (owner_id)
    KBTextBlockVersion  → KBTextBlock        (block_id)       → KBData
    KBTextTable         → KBTextBlockVersion (version_id)     → … → KBData
    KBLifecycleHistory  → KBData             (data_id)
    KBTable             → KBData             (owner_id)
    KBWarehouseConfig   → KBWarehouse        (warehouse_id)
    KBQdrantCollection  → KBQdrantConnection (connection_id)

Because these tables were NOT covered, `app_user` could read another tenant's
rows by querying a child table directly (isolation only held as long as every
query joined through the tenant-scoped parent). This migration closes that gap:
each child table gets RLS ENABLED + FORCED with a `tenant_isolation_child`
policy whose predicate is an EXISTS that walks the FK chain up to the parent's
`tenant_id`, compared against the runtime setting `app.tenant_id` — the same
value the parent tables' own policy uses, so behaviour stays consistent.

asyncpg rejects multiple statements per prepared-statement call, so each DDL
statement is issued individually (same constraint the base RLS migration notes).

Test verification: tests/integration/test_rls_kb_child_tables.py

Emergency rollback: `alembic downgrade -1`.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "57abfbeabb56"
branch_labels = None
depends_on = None


# Runtime tenant, NULL when unset (→ no rows) instead of erroring on ''::uuid.
_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# child table → EXISTS predicate proving the row belongs to app.tenant_id.
# The child table is referenced by its quoted name so the predicate is
# unambiguous inside the policy body.
_CHILD_PREDICATES: dict[str, str] = {
    "KBTextBlock": (
        f'EXISTS (SELECT 1 FROM "KBData" d '
        f'WHERE d.data_id = "KBTextBlock".owner_id AND d.tenant_id = {_TID})'
    ),
    "KBTextBlockVersion": (
        f'EXISTS (SELECT 1 FROM "KBTextBlock" b '
        f'JOIN "KBData" d ON d.data_id = b.owner_id '
        f'WHERE b.block_id = "KBTextBlockVersion".block_id AND d.tenant_id = {_TID})'
    ),
    "KBTextTable": (
        f'EXISTS (SELECT 1 FROM "KBTextBlockVersion" v '
        f'JOIN "KBTextBlock" b ON b.block_id = v.block_id '
        f'JOIN "KBData" d ON d.data_id = b.owner_id '
        f'WHERE v.version_id = "KBTextTable".version_id AND d.tenant_id = {_TID})'
    ),
    "KBLifecycleHistory": (
        f'EXISTS (SELECT 1 FROM "KBData" d '
        f'WHERE d.data_id = "KBLifecycleHistory".data_id AND d.tenant_id = {_TID})'
    ),
    "KBTable": (
        f'EXISTS (SELECT 1 FROM "KBData" d '
        f'WHERE d.data_id = "KBTable".owner_id AND d.tenant_id = {_TID})'
    ),
    "KBWarehouseConfig": (
        f'EXISTS (SELECT 1 FROM "KBWarehouse" w '
        f'WHERE w.warehouse_id = "KBWarehouseConfig".warehouse_id AND w.tenant_id = {_TID})'
    ),
    "KBQdrantCollection": (
        f'EXISTS (SELECT 1 FROM "KBQdrantConnection" c '
        f'WHERE c.connection_id = "KBQdrantCollection".connection_id AND c.tenant_id = {_TID})'
    ),
}

_POLICY = "tenant_isolation_child"


def upgrade() -> None:
    for table, predicate in _CHILD_PREDICATES.items():
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO app_user')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        # Idempotent: CREATE POLICY has no IF NOT EXISTS.
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(
            f'CREATE POLICY {_POLICY} ON "{table}" '
            f'AS PERMISSIVE FOR ALL TO PUBLIC '
            f'USING ({predicate}) WITH CHECK ({predicate})'
        )


def downgrade() -> None:
    for table in _CHILD_PREDICATES:
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON "{table}" FROM app_user')
