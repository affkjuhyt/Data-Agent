"""Schema scan writes structure, profiles and proposed FK relationships."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import psycopg2
import pytest
from psycopg2.extras import Json

from services.worker_agent import schema_scans
from services.worker_agent.events import Event
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]


async def test_schema_and_profile_scans_record_keys_stats_and_join_hints(
    seed: Seed, ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS scan_r1_03")
            cur.execute("DROP TABLE IF EXISTS scan_r1_03.orders")
            cur.execute("DROP TABLE IF EXISTS scan_r1_03.customers")
            cur.execute(
                "CREATE TABLE scan_r1_03.customers "
                "(id integer PRIMARY KEY, email text, branch text)"
            )
            cur.execute(
                "CREATE TABLE scan_r1_03.orders "
                "(id integer PRIMARY KEY, customer_id integer NOT NULL, amount numeric, "
                "CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) "
                "REFERENCES scan_r1_03.customers(id))"
            )
            cur.execute(
                "INSERT INTO scan_r1_03.customers VALUES "
                "(1, 'alice@example.com', 'HN'), (2, 'bob@example.com', NULL)"
            )
            cur.execute("INSERT INTO scan_r1_03.orders VALUES (10, 1, 120.50), (11, 1, NULL)")
            cur.execute("GRANT USAGE ON SCHEMA scan_r1_03 TO dw_reader")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA scan_r1_03 TO dw_reader")

    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Schema scan")
    user = seed.row(
        "org.app_user", org_id=org, external_id="scan-user", username="scan", display_name="Scan"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-SCAN", name="Scan", steward_id=user)
    source_id = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Schema scan source",
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options=Json({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/schema-scan",
        scan_schemas=["scan_r1_03"],
        status="active",
    )
    reader = StaticReader(
        {
            f"secret/tenants/{org}/sources/schema-scan": {
                "user": "dw_reader",
                "password": "warehouse-reader",
            }
        }
    )
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)

    schema_run = seed.row(
        "catalog.scan_run", org_id=org, source_id=source_id, kind="schema", trigger="manual"
    )
    event = Event(
        event_id=uuid.uuid4(),
        org_id=org,
        event_type="scan_run.created",
        aggregate_type="scan_run",
        aggregate_id=schema_run,
        payload={
            "scan_run_id": str(schema_run),
            "source_id": str(source_id),
            "requested_by": str(user),
        },
    )
    await schema_scans.scan_schema(event)

    customer = seed.one(
        "SELECT id FROM catalog.dataset WHERE source_id = %s AND table_name = 'customers'",
        source_id,
    )
    order = seed.one(
        "SELECT id FROM catalog.dataset WHERE source_id = %s AND table_name = 'orders'", source_id
    )
    assert customer and order
    assert seed.one(
        "SELECT is_key FROM catalog.column_meta WHERE dataset_id = %s AND name = 'id'", customer
    )
    assert (
        seed.one(
            "SELECT count(*) FROM context.relationship WHERE from_dataset_id = %s "
            "AND to_dataset_id = %s AND status = 'pending_review'",
            order,
            customer,
        )
        == 1
    )

    profile_run = seed.row(
        "catalog.scan_run", org_id=org, source_id=source_id, kind="profile", trigger="manual"
    )
    await schema_scans.scan_schema(
        Event(
            event_id=uuid.uuid4(),
            org_id=org,
            event_type="scan_run.created",
            aggregate_type="scan_run",
            aggregate_id=profile_run,
            payload={
                "scan_run_id": str(profile_run),
                "source_id": str(source_id),
                "requested_by": str(user),
            },
        )
    )
    email_profile = seed.one(
        "SELECT p.top_values, p.min_value FROM catalog.column_profile p "
        "JOIN catalog.column_meta c ON c.id = p.column_id "
        "WHERE c.name = 'email' AND p.scan_run_id = %s",
        profile_run,
    )
    assert email_profile[0] == [] and email_profile[1] is None
