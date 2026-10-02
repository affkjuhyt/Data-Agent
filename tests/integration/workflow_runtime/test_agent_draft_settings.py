"""Settings saved on an agent that has no draft version, against a real migrated Postgres.

The seeded agents (tests/e2e/seed.sql) have a published version and no draft.
update_agent_draft only UPDATEd the draft row, so turning Memory on matched no row,
answered 200, and the next publish shipped memory_enabled = false.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _seed_published_agent(dsn: str) -> tuple[str, str, str]:
    """A tenant and an agent with one published version and no draft, as seed.sql makes it."""
    tenant_id, agent_id, workflow_id, wv_id, av_id = (str(uuid.uuid4()) for _ in range(5))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "Tenants" (id, name, slug) VALUES (%s,%s,%s)',
                (tenant_id, "t", f"t-{tenant_id[:8]}"),
            )
            cur.execute(
                'INSERT INTO "Agents" (id, tenant_id, name) VALUES (%s,%s,%s)',
                (agent_id, tenant_id, "a"),
            )
            cur.execute(
                'INSERT INTO "Workflows" (id, agent_id, name) VALUES (%s,%s,%s)',
                (workflow_id, agent_id, "w"),
            )
            cur.execute(
                'INSERT INTO "WorkflowVersions" (id, workflow_id, version, status) VALUES (%s,%s,1,%s)',
                (wv_id, workflow_id, "published"),
            )
            cur.execute(
                'INSERT INTO "AgentVersions" (id, agent_id, version, status, workflow_version_id,'
                " responder_model, memory_enabled) VALUES (%s,%s,1,%s,%s,%s,false)",
                (av_id, agent_id, "published", wv_id, "picked-alias"),
            )
            cur.execute(
                'UPDATE "Agents" SET published_version_id=%s WHERE id=%s', (av_id, agent_id)
            )
    finally:
        conn.close()
    return tenant_id, agent_id, workflow_id


def _published_settings(dsn: str, agent_id: str) -> tuple[Any, ...]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT v.memory_enabled, v.responder_model FROM "Agents" a'
                ' JOIN "AgentVersions" v ON v.id = a.published_version_id WHERE a.id=%s',
                (agent_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


@pytest.fixture
async def repo(migrated_db: dict[str, str]) -> Any:
    from libs.database_connector.postgres_connector import client
    from services.workflow_runtime.infrastructure import authoring_repository as repo_mod

    client.set_url("postgresql+asyncpg://" + migrated_db["dsn"][len("postgresql://") :])
    await client.open()
    yield repo_mod
    await client.close()


async def test_memory_turned_on_without_a_draft_reaches_the_next_publish(
    repo: Any, migrated_db: dict[str, str]
) -> None:
    tenant_id, agent_id, workflow_id = _seed_published_agent(migrated_db["dsn"])

    await repo.update_agent_draft(agent_id, {"memory_enabled": True}, tenant_id=tenant_id)

    # The Settings tab reads the draft back; the untouched setting is carried over.
    agent = await repo.get_agent(agent_id, tenant_id=tenant_id)
    assert agent["draft_version_id"] and agent["memory_enabled"] is True
    assert agent["responder_model"] == "picked-alias"

    await repo.publish_agent(agent_id, workflow_id, tenant_id=tenant_id)
    assert _published_settings(migrated_db["dsn"], agent_id) == (True, "picked-alias")
