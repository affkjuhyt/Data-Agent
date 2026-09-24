"""Regression: `stamp_pipeline_status`'s original implementation compiled
correctly and passed its mocked unit tests, but against a real Postgres it
corrupted `KBData.metadata` into an array — `cast(json.dumps(patch), JSONB)`
hands asyncpg's JSONB codec an already-serialized *string*, which the codec
then serializes *again* into a JSON string scalar; Postgres's `||` between an
object and a scalar is array concatenation, not an object merge. Only a real
Postgres round-trip catches this class of bug — a mocked session's
`execute()` never actually asks Postgres what `||` does with the bound
value's real type.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Iterator

import psycopg2
import pytest

from tests.integration.test_web_crawler_worker import REPO_ROOT, _run_alembic

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS kb_structured CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute("DROP ROLE IF EXISTS app_user")
    finally:
        conn.close()
    yield postgres_container


@pytest.fixture
def migrated_db(fresh_db) -> dict[str, str]:
    result = _run_alembic(fresh_db["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return fresh_db


def _seed_kbdata(dsn: str, initial_metadata: dict) -> tuple[str, str]:
    tenant_id, role_row_id, data_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "Stamp Test Tenant", f"stamp-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "RolePermissions" (id, role_id, resource, action)
                   VALUES (%s, 'PLATFORM_ADMIN', '_role_anchor', 'label')""",
                (role_row_id,),
            )
            cur.execute(
                """INSERT INTO "KBData"
                       (data_id, tenant_id, role_id, name, extension, language, source_type,
                        added_by, abstract, metadata, current_tier)
                   VALUES (%s, %s, %s, 'Test Doc', 'xls', 'english', 'doc', 'tester', '', %s::jsonb, 'bronze')""",
                (data_id, tenant_id, role_row_id, json.dumps(initial_metadata)),
            )
    finally:
        conn.close()
    return tenant_id, data_id


@pytest.fixture
def postgres_client_module():
    """Same sys.path juggling as test_web_crawler_worker.py's worker_module
    fixture: importing `libs.*` needs `backend` on the path without the
    auth_api-polluted entries this repo's conftest otherwise leaves in place."""
    polluting = [str(REPO_ROOT / "backend" / "services"), str(REPO_ROOT / "backend" / "services" / "auth_api")]
    removed = [p for p in polluting if p in sys.path]
    for p in removed:
        sys.path.remove(p)
    added = str(REPO_ROOT / "backend")
    added_to_path = added not in sys.path
    if added_to_path:
        sys.path.insert(0, added)
    try:
        from libs.database_connector.postgres_connector import PostgresClient
        from libs.ingestion.pipeline_status import stamp_pipeline_status
        yield PostgresClient, stamp_pipeline_status
    finally:
        if added_to_path and added in sys.path:
            sys.path.remove(added)
        for p in removed:
            if p not in sys.path:
                sys.path.insert(0, p)


def _read_metadata(dsn: str, data_id: str) -> tuple[str, dict]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT jsonb_typeof(metadata), metadata FROM "KBData" WHERE data_id = %s', (data_id,))
            kind, metadata = cur.fetchone()
            return kind, metadata
    finally:
        conn.close()


def test_stamping_a_failure_merges_into_the_existing_object_not_an_array(migrated_db, postgres_client_module) -> None:
    PostgresClient, stamp_pipeline_status = postgres_client_module
    _tenant_id, data_id = _seed_kbdata(migrated_db["dsn"], {"doc_type": "xls", "source_id": "s1"})

    async def run() -> None:
        client = PostgresClient()
        client.set_url(migrated_db["dsn"])
        await client.open()
        try:
            await stamp_pipeline_status(client, data_id, stage="silver", status="failed", error="boom")
        finally:
            await client.close()

    asyncio.run(run())

    kind, metadata = _read_metadata(migrated_db["dsn"], data_id)
    assert kind == "object", f"metadata became a {kind}, not an object: {metadata}"
    assert metadata["doc_type"] == "xls"  # original field survives the merge
    assert metadata["last_pipeline_stage"] == "silver"
    assert metadata["last_pipeline_status"] == "failed"
    assert metadata["last_pipeline_error"] == "boom"


def test_a_second_stamp_overwrites_the_first_without_accumulating(migrated_db, postgres_client_module) -> None:
    PostgresClient, stamp_pipeline_status = postgres_client_module
    _tenant_id, data_id = _seed_kbdata(migrated_db["dsn"], {"doc_type": "pdf"})

    async def run() -> None:
        client = PostgresClient()
        client.set_url(migrated_db["dsn"])
        await client.open()
        try:
            await stamp_pipeline_status(client, data_id, stage="silver", status="running")
            await stamp_pipeline_status(client, data_id, stage="silver", status="succeeded")
        finally:
            await client.close()

    asyncio.run(run())

    kind, metadata = _read_metadata(migrated_db["dsn"], data_id)
    assert kind == "object"
    assert metadata["last_pipeline_status"] == "succeeded"
    assert metadata["last_pipeline_error"] is None
