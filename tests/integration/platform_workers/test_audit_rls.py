"""The audit writers under row-level security.

The workers used to connect as the superuser, which skips every policy. As
app_user (NOBYPASSRLS) an insert into a tenant table is rejected unless
app.tenant_id names the row's tenant, so these run the real writers against
the migrated schema as app_user. The bridge's batch mixes tenants inside one
transaction — the case where a row flushed under the wrong tenant would fail.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg2
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from services.platform_workers.workers.audit import bridge, data_access

pytestmark = pytest.mark.integration

_APP_USER_PASSWORD = "app-user-test-pw"
TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


@pytest.fixture
def app_user_sessions(migrated_db) -> Iterator[sessionmaker[Session]]:
    """Sessions as app_user, with the grants grant-roles.sql gives it on the cluster."""
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER ROLE app_user LOGIN PASSWORD '{_APP_USER_PASSWORD}'")
            cur.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"
            )
            cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user")
            for tenant in (TENANT_A, TENANT_B):
                cur.execute(
                    'INSERT INTO "Tenants" (id, name, slug) VALUES (%s, %s, %s)',
                    (tenant, "Audit RLS", f"audit-rls-{tenant[:8]}"),
                )
    finally:
        conn.close()

    url = make_url(migrated_db["dsn"]).set(
        drivername="postgresql+psycopg2", username="app_user", password=_APP_USER_PASSWORD
    )
    engine = create_engine(url, future=True)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


def _rows(dsn: str, sql: str) -> list[tuple[Any, ...]]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())
    finally:
        conn.close()


def test_a_batch_of_two_tenants_writes_each_row_under_its_tenant(
    app_user_sessions, migrated_db
) -> None:
    tenant_of = {"user-a": TENANT_A, "user-b": TENANT_B}
    events = [
        {"id": f"ev-{n}", "type": "LOGIN", "userId": user, "time": n}
        for n, user in enumerate(["user-a", "user-b", "user-a"])
    ]

    with app_user_sessions() as session:
        written = bridge.write_events(session, events, tenant_of.get)

    assert written == 3
    assert sorted(
        _rows(migrated_db["dsn"], 'SELECT source_event_id, tenant_id::text FROM "AuditLogs"')
    ) == [
        ("ev-0", TENANT_A),
        ("ev-1", TENANT_B),
        ("ev-2", TENANT_A),
    ]


def test_a_data_access_event_is_written_under_its_tenant(app_user_sessions, migrated_db) -> None:
    event = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": TENANT_B,
        "user_id": "user-b",
        "action": "kb_retrieve",
        "decision": "allow",
    }

    with app_user_sessions() as session:
        assert data_access.insert_data_access(session, event)
        session.commit()

    assert _rows(migrated_db["dsn"], 'SELECT tenant_id::text FROM "DataAccessLog"') == [(TENANT_B,)]
