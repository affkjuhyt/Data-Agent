"""A conversation turn saved and read back under row-level security.

Messages and AgentTraces carry no tenant_id; since 7d2a5f9c4e6b their policy
goes through the conversation's tenant. save_message and save_trace used to
insert in an unscoped session, which that policy rejects, so every turn would
have gone unsaved. Run as app_user (NOBYPASSRLS), like runtime_writer on the
cluster.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def runtime_db(migrated_db: dict[str, str], app_user_dsn: str) -> Any:
    from libs.database_connector.postgres_connector import client
    from services.workflow_runtime.infrastructure import db

    client.set_url(app_user_dsn)
    await client.open()
    yield db
    await client.close()


async def test_a_turn_is_saved_and_read_only_under_its_tenant(runtime_db: Any, migrated_db) -> None:
    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for t in (tenant, other):
                cur.execute(
                    'INSERT INTO "Tenants" (id, name, slug) VALUES (%s, %s, %s)',
                    (t, "t", f"t-{t[:8]}"),
                )
    finally:
        conn.close()

    conv_id = await runtime_db.create_conversation(user_ref="u1", tenant_id=tenant)
    assert conv_id
    await runtime_db.save_message(conv_id, "user", "giá vàng SJC?", tenant_id=tenant)
    reply_id = await runtime_db.save_message(conv_id, "assistant", "82,5 triệu", tenant_id=tenant)
    assert reply_id
    await runtime_db.save_trace(reply_id, 0, "responder", {"q": 1}, {"a": 2}, tenant_id=tenant)

    assert await runtime_db.get_messages(conv_id, tenant_id=tenant) == [
        {"role": "user", "content": "giá vàng SJC?"},
        {"role": "assistant", "content": "82,5 triệu"},
    ]
    assert await runtime_db.get_messages(conv_id, tenant_id=other) == []

    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT tool_name FROM "AgentTraces" WHERE message_id = %s', (reply_id,))
            assert cur.fetchall() == [("responder",)]
    finally:
        conn.close()
