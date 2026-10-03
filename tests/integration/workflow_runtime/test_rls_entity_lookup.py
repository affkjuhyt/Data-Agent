"""KBEntityLookup's row-level policy (migration 6c1f4a8e3b5d), as app_user.

Aliases are per tenant or global (tenant_id NULL): entity_registry resolves a
tenant's own alias before a global one. A tenant must see its rows and the
global ones, never another tenant's, and must not write a global row or
another tenant's.
"""

from __future__ import annotations

import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _insert(cur: psycopg2.extensions.cursor, alias: str, tenant: str | None) -> None:
    cur.execute(
        'INSERT INTO "KBEntityLookup" (lookup_id, alias_name, canonical_name, tenant_id)'
        " VALUES (%s, %s, %s, %s)",
        (str(uuid.uuid4()), alias, alias.upper(), tenant),
    )


def test_a_tenant_reads_its_aliases_and_the_global_ones_and_writes_only_its_own(
    migrated_db, app_user_dsn
) -> None:
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    owner = psycopg2.connect(migrated_db["dsn"])
    owner.autocommit = True
    try:
        with owner.cursor() as cur:
            for tenant in (tenant_a, tenant_b):
                cur.execute(
                    'INSERT INTO "Tenants" (id, name, slug) VALUES (%s, %s, %s)',
                    (tenant, "t", f"t-{tenant[:8]}"),
                )
            _insert(cur, "global", None)
            _insert(cur, "of-a", tenant_a)
            _insert(cur, "of-b", tenant_b)
    finally:
        owner.close()

    conn = psycopg2.connect(app_user_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_a,))
            cur.execute('SELECT alias_name FROM "KBEntityLookup" ORDER BY alias_name')
            assert [r[0] for r in cur.fetchall()] == ["global", "of-a"]

            _insert(cur, "new-of-a", tenant_a)
            for tenant in (None, tenant_b):
                cur.execute("SAVEPOINT refused")
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    _insert(cur, "refused", tenant)
                cur.execute("ROLLBACK TO SAVEPOINT refused")
        conn.commit()
    finally:
        conn.close()
