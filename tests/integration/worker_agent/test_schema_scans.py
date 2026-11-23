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
                "(id integer PRIMARY KEY, email text, branch text, ma_kh text, invoice_no text,"
                " product_name text)"
            )
            cur.execute(
                "CREATE TABLE scan_r1_03.orders "
                "(id integer PRIMARY KEY, customer_id integer NOT NULL, amount numeric, "
                "CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) "
                "REFERENCES scan_r1_03.customers(id))"
            )
            cur.execute(
                "INSERT INTO scan_r1_03.customers VALUES "
                "(1, 'alice@example.com', 'HN', '079203001234', '123456789012', 'Xe đạp'),"
                " (2, 'bob@example.com', NULL, '001185000123', '123456789013', 'Mũ')"
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

    # GEN-14: national ids under a name that says nothing are masked and labelled for review; a
    # 12-digit invoice number and a product name are sampled.
    def column(name: str) -> tuple[object, ...]:
        return seed.one(  # type: ignore[return-value]
            "SELECT p.top_values, p.min_value, c.pii_label, c.pii_confirmed"
            " FROM catalog.column_profile p JOIN catalog.column_meta c ON c.id = p.column_id"
            " WHERE c.name = %s AND p.scan_run_id = %s",
            name,
            profile_run,
        )

    ids = column("ma_kh")
    assert {v["value"] for v in ids[0]} == {"***"} and ids[1] is None
    assert ids[2:] == ("national_id", False)
    invoices = column("invoice_no")
    assert {v["value"] for v in invoices[0]} == {"123456789012", "123456789013"}
    assert invoices[2] is None
    assert {v["value"] for v in column("product_name")[0]} == {"Xe đạp", "Mũ"}


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


@pytest.mark.req("AC-04-03-02", "AC-04-03-03", "AC-04-03-07")
async def test_a_moved_structure_is_versioned_when_additive_and_proposed_when_breaking(
    seed: Seed, ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """UC-04-03 AC-03, AC-07 and AC-02 (RES-22)."""
    from services.worker_agent import proposals

    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)

    def sql(*statements: str) -> None:
        with psycopg2.connect(dsn) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                for s in statements:
                    cur.execute(s)

    sql(
        "CREATE SCHEMA IF NOT EXISTS scan_uc0403",
        "DROP TABLE IF EXISTS scan_uc0403.loans",
        "CREATE TABLE scan_uc0403.loans (id integer PRIMARY KEY, du_no numeric)",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') "
        "THEN CREATE ROLE dw_reader LOGIN; END IF; END $$",
        "ALTER ROLE dw_reader PASSWORD 'warehouse-reader'",
        "GRANT USAGE ON SCHEMA scan_uc0403 TO dw_reader",
        "GRANT SELECT ON ALL TABLES IN SCHEMA scan_uc0403 TO dw_reader",
    )
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Schema moves")
    user = seed.row(
        "org.app_user", org_id=org, external_id="moves", username="moves", display_name="Moves"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-MOVE", name="Moves", steward_id=user)
    source_id = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Moves source",
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options=Json({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/moves",
        scan_schemas=["scan_uc0403"],
        status="active",
    )
    reader = StaticReader(
        {
            f"secret/tenants/{org}/sources/moves": {
                "user": "dw_reader",
                "password": "warehouse-reader",
            }
        }
    )
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)

    async def scan() -> None:
        run = seed.row(
            "catalog.scan_run", org_id=org, source_id=source_id, kind="schema", trigger="manual"
        )
        await schema_scans.scan_schema(
            Event(
                uuid.uuid4(),
                org,
                "scan_run.created",
                "scan_run",
                run,
                {"scan_run_id": str(run), "source_id": str(source_id), "requested_by": str(user)},
            )
        )

    await scan()
    loans = seed.one("SELECT id FROM catalog.dataset WHERE source_id = %s", source_id)
    sql("ALTER TABLE scan_uc0403.loans ADD COLUMN branch text")
    await scan()
    # AC-03: an added column is a new version at once, without a proposal.
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", loans) == 2
    assert (
        seed.one(
            "SELECT change_kind FROM catalog.dataset_version WHERE dataset_id = %s AND version = 2",
            loans,
        )
        == "additive"
    )
    assert seed.one(
        "SELECT valid_to IS NOT NULL FROM catalog.dataset_version"
        " WHERE dataset_id = %s AND version = 1",
        loans,
    )
    assert (
        seed.one(
            "SELECT dataset_version FROM catalog.column_meta WHERE dataset_id = %s"
            " AND name = 'branch'",
            loans,
        )
        == 2
    )

    # A personal-data label a steward set on the column must survive its retyping.
    sql(
        "UPDATE catalog.column_meta SET pii_label = 'account_no', pii_confirmed = true"
        f" WHERE dataset_id = '{loans}' AND name = 'du_no'"
    )
    sql("ALTER TABLE scan_uc0403.loans ALTER COLUMN du_no TYPE text")
    await scan()
    await scan()
    # AC-07: a retyped column waits for a person, once, and nothing is written.
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", loans) == 2
    proposal = seed.one(
        "SELECT id FROM inbox.proposal WHERE target_ref = %s AND kind = 'schema_change'"
        " AND status = 'pending_review'",
        loans,
    )
    assert proposal
    assert seed.one("SELECT count(*) FROM inbox.proposal WHERE target_ref = %s", loans) == 1

    # AC-02: accepted, the version is written and the old column closed, not deleted.
    sql(f"UPDATE inbox.proposal SET status = 'accepted' WHERE id = '{proposal}'")
    await proposals.decided(
        Event(
            uuid.uuid4(),
            org,
            "proposal.decided",
            "proposal",
            proposal,
            {"proposal_id": str(proposal), "status": "accepted", "reviewer_id": str(user)},
        )
    )
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", loans) == 3
    assert (
        seed.one(
            "SELECT removed_in_version FROM catalog.column_meta WHERE dataset_id = %s"
            " AND name = 'du_no' AND data_type = 'numeric'",
            loans,
        )
        == 3
    )
    assert (
        seed.one(
            "SELECT dataset_version FROM catalog.column_meta WHERE dataset_id = %s"
            " AND name = 'du_no' AND data_type = 'text'",
            loans,
        )
        == 3
    )
    assert seed.one(
        "SELECT pii_label, pii_confirmed FROM catalog.column_meta WHERE dataset_id = %s"
        " AND name = 'du_no' AND data_type = 'text'",
        loans,
    ) == ("account_no", False)
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "applied"


@pytest.mark.req("DF-10")
async def test_a_profile_keeps_quantiles_a_histogram_lengths_patterns_and_the_source_activity(
    seed: Seed, ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS scan_df10")
            cur.execute("DROP TABLE IF EXISTS scan_df10.sales")
            cur.execute(
                "CREATE TABLE scan_df10.sales (id integer PRIMARY KEY, order_no text,"
                " amount numeric, ordered_on date, note text, ma_kh text)"
            )
            # 100 rows: amount 1..100, an order number SO + 5 digits, a date a day apart, a free
            # note, and national ids under a name that says nothing (masked by their values).
            cur.execute(
                "INSERT INTO scan_df10.sales SELECT g, 'SO' || (43000 + g)::text, g,"
                " date '2024-01-01' + g - 1,"
                " CASE WHEN g % 2 = 0 THEN 'giao nhanh' ELSE 'Khách hẹn lại lần ' || g END,"
                " lpad((79203000000 + g)::text, 12, '0')"
                " FROM generate_series(1, 100) g"
            )
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') "
                "THEN CREATE ROLE dw_reader LOGIN; END IF; END $$"
            )
            cur.execute("ALTER ROLE dw_reader PASSWORD 'warehouse-reader'")
            cur.execute("GRANT USAGE ON SCHEMA scan_df10 TO dw_reader")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA scan_df10 TO dw_reader")

    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="DF-10")
    user = seed.row(
        "org.app_user", org_id=org, external_id="df10-user", username="df10", display_name="D"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-DF10", name="DF10", steward_id=user)
    source_id = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="DF-10 source",
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options=Json({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/df10",
        scan_schemas=["scan_df10"],
        status="active",
    )
    reader = StaticReader(
        {
            f"secret/tenants/{org}/sources/df10": {
                "user": "dw_reader",
                "password": "warehouse-reader",
            }
        }
    )
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)

    async def scan(kind: str) -> uuid.UUID:
        run = seed.row(
            "catalog.scan_run", org_id=org, source_id=source_id, kind=kind, trigger="manual"
        )
        await schema_scans.scan_schema(
            Event(
                event_id=uuid.uuid4(),
                org_id=org,
                event_type="scan_run.created",
                aggregate_type="scan_run",
                aggregate_id=run,
                payload={
                    "scan_run_id": str(run),
                    "source_id": str(source_id),
                    "requested_by": str(user),
                },
            )
        )
        return run

    await scan("schema")
    profile_run = await scan("profile")

    def profile(name: str) -> tuple[object, ...]:
        return seed.one(  # type: ignore[return-value]
            "SELECT p.quantiles, p.histogram, p.length_stats, p.value_patterns, p.pattern_hint"
            " FROM catalog.column_profile p JOIN catalog.column_meta c ON c.id = p.column_id"
            " WHERE c.name = %s AND p.scan_run_id = %s",
            name,
            profile_run,
        )

    quantiles, histogram, lengths, _, hint = profile("amount")
    assert quantiles == {"p05": 5.95, "p25": 25.75, "p50": 50.5, "p75": 75.25, "p95": 95.05}
    assert histogram["lo"] == 1 and histogram["hi"] == 100  # type: ignore[index]
    assert len(histogram["bins"]) == 20 and sum(histogram["bins"]) == 100  # type: ignore[index]
    assert lengths is None and hint is None

    dates = profile("ordered_on")
    assert dates[0]["p50"] == "2024-02-19" and dates[1] is None  # type: ignore[index]

    order_no = profile("order_no")
    assert order_no[0] is None and order_no[2] == {"min": 7, "max": 7, "avg": 7.0}
    assert order_no[3] == [{"pattern": r"[A-Z]{2}\d{5}", "share": 1.0}]
    assert order_no[4] == r"[A-Z]{2}\d{5}"

    # Free text has shapes but no single one: no pattern_hint.
    note = profile("note")
    assert note[4] is None and len(note[3]) == 3  # type: ignore[arg-type]

    # A column personal by its values keeps no distribution; its shape stays.
    ids = profile("ma_kh")
    assert ids[0] is None and ids[1] is None and ids[4] == r"\d{12}"

    # A measure a column has not is SQL NULL, so "quantiles IS NULL" finds it.
    assert (
        seed.one(
            "SELECT count(*) FROM catalog.column_profile WHERE scan_run_id = %s"
            " AND (quantiles = 'null'::jsonb OR histogram = 'null'::jsonb"
            " OR length_stats = 'null'::jsonb OR value_patterns = 'null'::jsonb)",
            profile_run,
        )
        == 0
    )

    activity = seed.one(
        "SELECT source_activity FROM catalog.dataset WHERE source_id = %s AND table_name = 'sales'",
        source_id,
    )
    assert activity is not None
    assert {"n_tup_ins", "n_live_tup", "last_autoanalyze", "observed_at"} <= set(activity)


@pytest.mark.req("DF-11")
async def test_a_profile_proposes_draft_rules_once_and_keeps_the_pii_evidence(
    seed: Seed, ddl_server: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The DF-10 test's table and source, profiled twice: the second scan proposes nothing new.
    await test_a_profile_keeps_quantiles_a_histogram_lengths_patterns_and_the_source_activity(
        seed, ddl_server, monkeypatch
    )
    sales = seed.one(
        "SELECT id FROM catalog.dataset WHERE schema_name = 'scan_df10' AND table_name = 'sales'"
        " ORDER BY created_at DESC LIMIT 1"
    )
    rules = seed.all(
        "SELECT column_name, kind, params, origin, status FROM ingest.quality_rule"
        " WHERE dataset_id = %s ORDER BY column_name, kind",
        sales,
    )
    by = {(r[0], r[1]): r for r in rules}
    assert all(r[3] == "agent_suggested" and r[4] == "draft" for r in rules)
    assert ("order_no", "unique") in by and ("order_no", "not_null") in by
    assert by[("order_no", "match_regex")][2] == {"regex": r"^[A-Z]{2}\d{5}$", "mostly": 0.99}
    # The key is the source's to promise; a free note is unique by none of its values.
    assert not any(r[0] == "id" for r in rules)
    assert ("note", "unique") not in by
    # A personal column's values are in no rule.
    assert ("ma_kh", "in_list") not in by
    assert not any("07920300" in str(r[2]) for r in rules)

    evidence = seed.one(
        "SELECT c.pii_evidence FROM catalog.column_meta c WHERE c.dataset_id = %s"
        " AND c.name = 'ma_kh'",
        sales,
    )
    assert evidence["share"] >= 0.5 and evidence["samples_checked"] > 0  # type: ignore[index]
    assert "scan_run_id" in evidence  # type: ignore[operator]

    source = seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", sales)
    space = seed.one("SELECT data_space_id FROM catalog.dataset WHERE id = %s", sales)
    org = seed.one("SELECT org_id FROM catalog.dataset WHERE id = %s", sales)
    run = seed.row(
        "catalog.scan_run", org_id=org, source_id=source, kind="profile", trigger="manual"
    )
    assert space is not None
    await schema_scans.scan_schema(
        Event(
            event_id=uuid.uuid4(),
            org_id=org,
            event_type="scan_run.created",
            aggregate_type="scan_run",
            aggregate_id=run,
            payload={"scan_run_id": str(run), "source_id": str(source)},
        )
    )
    assert seed.one("SELECT count(*) FROM ingest.quality_rule WHERE dataset_id = %s", sales) == len(
        rules
    )
