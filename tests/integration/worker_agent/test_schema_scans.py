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
            # The reader this test logs in as, made here: it was made only by test_executor.py, so
            # this test failed whenever it ran without that one first.
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') "
                "THEN CREATE ROLE dw_reader LOGIN; END IF; END $$"
            )
            cur.execute("ALTER ROLE dw_reader PASSWORD 'warehouse-reader'")
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


async def test_a_category_column_is_profiled_whole_for_the_value_index(
    ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # GEN-23: the index took the profile's top ten, so a filter on the eleventh branch was not
    # recognised. A category keeps every value; a column past the cap keeps none for the index.
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS scan_gen23")
            cur.execute("DROP TABLE IF EXISTS scan_gen23.sales")
            cur.execute("CREATE TABLE scan_gen23.sales (branch text, ref text)")
            cur.execute(
                "INSERT INTO scan_gen23.sales SELECT 'B' || (i % 15), 'R' || i"
                " FROM generate_series(1, 600) AS i"
            )
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') "
                "THEN CREATE ROLE dw_reader LOGIN; END IF; END $$"
            )
            cur.execute("ALTER ROLE dw_reader PASSWORD 'warehouse-reader'")
            cur.execute("GRANT USAGE ON SCHEMA scan_gen23 TO dw_reader")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA scan_gen23 TO dw_reader")
    org = uuid.uuid4()
    source = schema_scans.catalog.Source(
        org_id=org,
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options={"sslmode": "disable"},
        secret_ref=f"secret/tenants/{org}/sources/gen23",
        query_timeout_s=30,
    )
    reader = StaticReader(
        {
            f"secret/tenants/{org}/sources/gen23": {
                "user": "dw_reader",
                "password": "warehouse-reader",
            }
        }
    )
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)
    table = {
        "schema_name": "scan_gen23",
        "table_name": "sales",
        "columns": [{"name": "branch"}, {"name": "ref"}],
    }
    profiles = await schema_scans._profile_source(source, [table])
    branch = profiles[("scan_gen23", "sales", "branch")]
    assert len(branch["top_values"]) == 10 and len(branch["category_values"]) == 15
    ref = profiles[("scan_gen23", "sales", "ref")]
    assert len(ref["top_values"]) == 10 and ref["category_values"] == []


async def test_columns_postgres_cannot_order_or_compare_are_profiled_as_text(
    ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # AdventureWorksDW's flag columns are boolean: min(boolean) does not exist, and the error
    # failed the whole profile run. xml and json have no equality either (count DISTINCT).
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS scan_types")
            cur.execute("DROP TABLE IF EXISTS scan_types.product")
            cur.execute(
                "CREATE TABLE scan_types.product"
                " (finished boolean, spec xml, extra json, qty integer, photo bytea)"
            )
            cur.execute(
                "INSERT INTO scan_types.product VALUES"
                " (true, '<a/>', '{\"k\": 1}', 9, '\\x4749'), (false, '<b/>', '{\"k\": 2}', 10, NULL),"
                " (true, NULL, NULL, 10, '\\x4749')"
            )
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') "
                "THEN CREATE ROLE dw_reader LOGIN; END IF; END $$"
            )
            cur.execute("ALTER ROLE dw_reader PASSWORD 'warehouse-reader'")
            cur.execute("GRANT USAGE ON SCHEMA scan_types TO dw_reader")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA scan_types TO dw_reader")
    org = uuid.uuid4()
    source = schema_scans.catalog.Source(
        org_id=org,
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options={"sslmode": "disable"},
        secret_ref=f"secret/tenants/{org}/sources/types",
        query_timeout_s=30,
    )
    reader = StaticReader(
        {
            f"secret/tenants/{org}/sources/types": {
                "user": "dw_reader",
                "password": "warehouse-reader",
            }
        }
    )
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)
    table = {
        "schema_name": "scan_types",
        "table_name": "product",
        "columns": [
            {"name": "finished", "data_type": "boolean"},
            {"name": "spec", "data_type": "xml"},
            {"name": "extra", "data_type": "json"},
            {"name": "qty", "data_type": "integer"},
            {"name": "photo", "data_type": "bytea"},
        ],
    }
    profiles = await schema_scans._profile_source(source, [table])
    finished = profiles[("scan_types", "product", "finished")]
    assert (finished["min_value"], finished["max_value"], finished["distinct_est"]) == (
        "false",
        "true",
        2,
    )
    assert profiles[("scan_types", "product", "spec")]["distinct_est"] == 2
    assert profiles[("scan_types", "product", "extra")]["distinct_est"] == 2
    # A number keeps its own order: 9 before 10, which as text would be the other way round.
    assert profiles[("scan_types", "product", "qty")]["min_value"] == "9"
    # A photo is counted, never sampled: no bounds, no values for the index.
    photo = profiles[("scan_types", "product", "photo")]
    assert (photo["distinct_est"], photo["min_value"], photo["top_values"]) == (1, None, [])
    assert photo["category_values"] == []
