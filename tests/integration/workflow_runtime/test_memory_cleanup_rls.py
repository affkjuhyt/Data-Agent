"""The memory-cleanup job as app_user, against the migrated schema.

The memory tables FORCE row-level security. The job used to run as the superuser
with unscoped sessions; as app_user those match no rows, and the job "succeeded"
without deleting anything. It now cleans one tenant at a time with the tenant set.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _seed_tenant(cur: Any) -> dict[str, str]:
    tenant, agent = str(uuid.uuid4()), str(uuid.uuid4())
    cur.execute(
        'INSERT INTO "Tenants" (id, name, slug) VALUES (%s, %s, %s)',
        (tenant, "Memory cleanup", f"mem-cleanup-{tenant[:8]}"),
    )
    cur.execute(
        'INSERT INTO "Agents" (id, tenant_id, name) VALUES (%s, %s, %s)', (agent, tenant, "a")
    )
    memory = (
        'INSERT INTO "AgentMemories" (id, tenant_id, agent_id, scope, memory_type, content,'
        " fact_key, expires_at, deleted_at)"
        " VALUES (%s, %s, %s, 'user', 'semantic', %s, %s, %s, %s)"
    )
    cur.execute(memory, (str(uuid.uuid4()), tenant, agent, "expired", "a", "2020-01-01", None))
    cur.execute(
        memory, (str(uuid.uuid4()), tenant, agent, "old tombstone", "b", None, "2020-01-01")
    )
    cur.execute(memory, (str(uuid.uuid4()), tenant, agent, "live", "c", None, None))
    episode = (
        'INSERT INTO "EpisodicMemory" (id, tenant_id, agent_id, content, expires_at)'
        " VALUES (%s, %s, %s, %s, %s)"
    )
    cur.execute(episode, (str(uuid.uuid4()), tenant, agent, "expired", "2020-01-01"))
    cur.execute(episode, (str(uuid.uuid4()), tenant, agent, "live", None))
    return {"tenant": tenant, "agent": agent}


def _remaining(dsn: str, table: str) -> list[tuple[str, str]]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT tenant_id::text, content FROM "{table}" ORDER BY 1, 2')
            return list(cur.fetchall())
    finally:
        conn.close()


async def test_every_tenant_is_cleaned_as_app_user(migrated_db, app_user_dsn, monkeypatch) -> None:
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            tenants = sorted([_seed_tenant(cur)["tenant"], _seed_tenant(cur)["tenant"]])
    finally:
        conn.close()

    from services.workflow_runtime.jobs import memory_cleanup

    monkeypatch.setenv("DATABASE_URL", app_user_dsn)
    monkeypatch.setenv("STRICT_TENANT_ISOLATION", "1")
    await memory_cleanup.run()

    live = [(t, "live") for t in tenants]
    assert _remaining(migrated_db["dsn"], "AgentMemories") == live
    assert _remaining(migrated_db["dsn"], "EpisodicMemory") == live
