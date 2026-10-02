"""Concurrent writers of one workflow's versions, against a real migrated Postgres.

publish / republish archive the published version then flip the target, and
create_draft_version checks "no draft yet" before inserting. Without a lock two
racing requests could both pass those checks; the repository now locks the parent
workflow row and the partial unique indexes (migration f7a1c3e5b9d2) back it up.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _seed(dsn: str, statuses: list[str]) -> tuple[str, str, list[str]]:
    """A tenant, an agent, one workflow and one WorkflowVersion per entry of `statuses`."""
    tenant_id, agent_id, workflow_id = (str(uuid.uuid4()) for _ in range(3))
    version_ids = [str(uuid.uuid4()) for _ in statuses]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s,%s,%s,%s)',
                        (tenant_id, "t", f"t-{tenant_id[:8]}", "Asia-SE1"))
            cur.execute('INSERT INTO "Agents" (id, tenant_id, name) VALUES (%s,%s,%s)',
                        (agent_id, tenant_id, "a"))
            cur.execute('INSERT INTO "Workflows" (id, agent_id, name) VALUES (%s,%s,%s)',
                        (workflow_id, agent_id, "w"))
            for i, (vid, status) in enumerate(zip(version_ids, statuses, strict=True), start=1):
                cur.execute(
                    'INSERT INTO "WorkflowVersions" (id, workflow_id, version, status) VALUES (%s,%s,%s,%s)',
                    (vid, workflow_id, i, status),
                )
    finally:
        conn.close()
    return tenant_id, workflow_id, version_ids


def _statuses(dsn: str, workflow_id: str) -> list[str]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT status FROM "WorkflowVersions" WHERE workflow_id=%s ORDER BY version', (workflow_id,))
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture
async def repo(migrated_db: dict[str, str]) -> Any:
    from libs.database_connector.postgres_connector import client
    from services.workflow_runtime.infrastructure import authoring_repository as repo_mod

    client.set_url("postgresql+asyncpg://" + migrated_db["dsn"][len("postgresql://"):])
    await client.open()
    yield repo_mod
    await client.close()


def test_the_database_refuses_a_second_published_or_draft_version(migrated_db: dict[str, str]) -> None:
    _, workflow_id, _ = _seed(migrated_db["dsn"], ["published", "draft"])
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        for status in ("published", "draft"):
            with conn.cursor() as cur, pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    'INSERT INTO "WorkflowVersions" (id, workflow_id, version, status) VALUES (%s,%s,%s,%s)',
                    (str(uuid.uuid4()), workflow_id, 10, status),
                )
    finally:
        conn.close()


async def test_racing_republishes_leave_exactly_one_published(repo: Any, migrated_db: dict[str, str]) -> None:
    tenant_id, workflow_id, (_, b, c) = _seed(migrated_db["dsn"], ["published", "archived", "archived"])

    results = await asyncio.gather(
        repo.republish_workflow_version(b, tenant_id=tenant_id),
        repo.republish_workflow_version(c, tenant_id=tenant_id),
        return_exceptions=True,
    )

    # Serialised by the workflow lock: both succeed one after the other, and the
    # last one wins. Never two published.
    assert not [r for r in results if isinstance(r, Exception)], results
    assert _statuses(migrated_db["dsn"], workflow_id).count("published") == 1


async def test_racing_draft_creation_makes_one_draft(repo: Any, migrated_db: dict[str, str]) -> None:
    tenant_id, workflow_id, _ = _seed(migrated_db["dsn"], ["published"])

    results = await asyncio.gather(
        repo.create_draft_version(workflow_id, tenant_id=tenant_id),
        repo.create_draft_version(workflow_id, tenant_id=tenant_id),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, (ValueError, repo.ConflictError))]
    assert len(ok) == 1 and len(refused) == 1, results
    assert _statuses(migrated_db["dsn"], workflow_id).count("draft") == 1


@pytest.mark.parametrize("operation", ["publish", "republish"])
async def test_version_writers_wait_for_the_workflow_lock(
    repo: Any, migrated_db: dict[str, str], operation: str
) -> None:
    """The point of the lock, observed directly: while another transaction holds the
    workflow row, a writer of its versions does not proceed (racing them and checking
    the end state would pass even without the lock, thanks to the unique indexes,
    only with a ConflictError instead of a clean retry). Not parametrised over draft
    creation: its INSERT takes a FK lock on the workflow row, so it would block behind
    the holder with or without the explicit lock."""
    from libs.database_connector.postgres_connector import client

    tenant_id, workflow_id, (pub, arch, draft) = _seed(migrated_db["dsn"], ["published", "archived", "draft"])
    if operation == "publish":
        call = lambda: repo.publish_workflow_version(draft, tenant_id=tenant_id)  # noqa: E731
    else:
        call = lambda: repo.republish_workflow_version(arch, tenant_id=tenant_id)  # noqa: E731

    async with client.get_client(tenant_id=tenant_id) as holder:
        async with repo._txn(holder):
            await repo._lock_workflow(holder, uuid.UUID(workflow_id))
            task = asyncio.create_task(call())
            await asyncio.sleep(0.6)
            assert not task.done(), f"{operation} ran while the workflow row was locked"
    await asyncio.wait_for(task, timeout=10)
