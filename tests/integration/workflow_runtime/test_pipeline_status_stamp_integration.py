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
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _seed_kbdata(dsn: str, initial_metadata: dict) -> tuple[str, str]:
    tenant_id, role_row_id, data_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug) VALUES (%s, %s, %s)""",
                (tenant_id, "Stamp Test Tenant", f"stamp-test-{tenant_id[:8]}"),
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
    from libs.database_connector.postgres_connector import PostgresClient
    from libs.ingestion.pipeline_status import stamp_pipeline_status
    return PostgresClient, stamp_pipeline_status


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
