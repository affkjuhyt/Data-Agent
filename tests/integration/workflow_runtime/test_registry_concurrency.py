"""Source / warehouse registry invariants against a real migrated Postgres.

Migration b1d3f5a7c9e2 makes "one active config per source / warehouse" and "one live
source per title" database rules; update_source_config locks the source row so two
concurrent edits are applied one after the other instead of the later one silently
overwriting the earlier.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _seed_source(dsn: str, title: str = "EIA Brent") -> tuple[str, str, str]:
    tenant_id, role_id, source_id = (str(uuid.uuid4()) for _ in range(3))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO "Tenants" (id, name, slug) VALUES (%s,%s,%s)',
                        (tenant_id, "t", f"t-{tenant_id[:8]}"))
            cur.execute('INSERT INTO "RolePermissions" (id, role_id, resource, action) VALUES (%s,%s,%s,%s)',
                        (role_id, "AI_ENGINEER", "kb", "read"))
            cur.execute(
                'INSERT INTO "KBSource" (source_id, tenant_id, role_id, connector_type, title) VALUES (%s,%s,%s,%s,%s)',
                (source_id, tenant_id, role_id, "http_file", title),
            )
            cur.execute(
                'INSERT INTO "KBSourceConfig" (source_id, version_number, is_active, config) VALUES (%s,1,true,%s::jsonb)',
                (source_id, '{"url": "https://example.invalid/a.csv"}'),
            )
    finally:
        conn.close()
    return tenant_id, role_id, source_id


def _refused(dsn: str, sql: str, params: tuple[Any, ...]) -> bool:
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
            except psycopg2.errors.UniqueViolation:
                return True
        return False
    finally:
        conn.close()


def test_the_database_refuses_a_second_active_config_and_a_duplicate_live_title(migrated_db: dict[str, str]) -> None:
    dsn = migrated_db["dsn"]
    tenant_id, role_id, source_id = _seed_source(dsn)

    assert _refused(dsn, 'INSERT INTO "KBSourceConfig" (source_id, version_number, is_active, config) '
                         "VALUES (%s, 2, true, '{}'::jsonb)", (source_id,))
    assert _refused(dsn, 'INSERT INTO "KBSource" (tenant_id, role_id, connector_type, title) VALUES (%s,%s,%s,%s)',
                    (tenant_id, role_id, "http_file", "EIA Brent"))
    # a deleted source frees its title, and an inactive second config is fine
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute('UPDATE "KBSource" SET is_deleted = true WHERE source_id = %s', (source_id,))
            cur.execute('INSERT INTO "KBSource" (tenant_id, role_id, connector_type, title) VALUES (%s,%s,%s,%s)',
                        (tenant_id, role_id, "http_file", "EIA Brent"))
    finally:
        conn.close()


async def test_concurrent_source_edits_are_applied_one_after_the_other(migrated_db: dict[str, str]) -> None:
    from libs.database_connector.postgres_connector import client
    from services.kb_backend.operations import source_ops

    dsn = migrated_db["dsn"]
    tenant_id, _, source_id = _seed_source(dsn)
    client.set_url("postgresql+asyncpg://" + dsn[len("postgresql://"):])
    await client.open()
    try:
        results = await asyncio.gather(
            source_ops.update_source_config(client, source_id, {"cron": "0 3 * * *"}, "u1", tenant_id),
            source_ops.update_source_config(client, source_id, {"notes": "second edit"}, "u2", tenant_id),
            return_exceptions=True,
        )
    finally:
        await client.close()

    assert not [r for r in results if isinstance(r, Exception)], results
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version_number, is_active, config FROM "KBSourceConfig" WHERE source_id=%s ORDER BY version_number',
                        (source_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    assert [(v, active) for v, active, _ in rows] == [(1, False), (2, False), (3, True)]
    final = rows[-1][2]
    # Neither edit was lost: the second was built on top of the first.
    assert final["cron"] == "0 3 * * *" and final["notes"] == "second edit"
