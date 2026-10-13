"""Queries run in place on a PostgreSQL source (here a schema of the test server reached as a
read-only login), recorded as query_run and query_result."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import psycopg2
import pytest

from libs.dataagent_db.models import catalog
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]

WAREHOUSE_PASSWORD = "warehouse-reader"


@pytest.fixture(scope="module")
def warehouse(ddl_server: dict[str, str]) -> dict[str, object]:
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS dw")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS dw.loan_balance "
            "(branch_code text, balance numeric(18,2), as_of_date date)"
        )
        cur.execute("TRUNCATE dw.loan_balance")
        cur.execute(
            "INSERT INTO dw.loan_balance VALUES ('HN', 1200.50, '2026-09-30'), "
            "('HCM', 2300.25, '2026-09-30'), ('DN', 400.00, '2026-09-30')"
        )
        cur.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_reader') THEN "
            "CREATE ROLE dw_reader LOGIN; END IF; END $$"
        )
        cur.execute("ALTER ROLE dw_reader PASSWORD %s", (WAREHOUSE_PASSWORD,))
        cur.execute("GRANT USAGE ON SCHEMA dw TO dw_reader")
        cur.execute("GRANT SELECT ON dw.loan_balance TO dw_reader")
    conn.close()
    parts = urlsplit(dsn)
    return {"host": parts.hostname, "port": parts.port, "database": parts.path.lstrip("/")}


def _source(
    seed: Seed, warehouse: dict[str, object], **over: object
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, catalog.Source, StaticReader]:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Tổ chức thử")
    user = seed.row("org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A")
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=user)
    ref = f"secret/tenants/{org}/sources/dw"
    source = catalog.Source(
        id=uuid.uuid4(),
        org_id=org,
        kind="postgresql",
        host=warehouse["host"],
        port=warehouse["port"],
        database_name=warehouse["database"],
        # The test server has no TLS; a real source would leave sslmode to its verify-full default.
        conn_options={"schema": "dw", "sslmode": "disable"},
        secret_ref=ref,
        max_concurrency=2,
        query_timeout_s=over.get("timeout", 5),
    )
    secrets = StaticReader({ref: {"user": "dw_reader", "password": WAREHOUSE_PASSWORD}})
    # The executor reads only tables that are datasets of the Data Space on this source.
    seed.row(
        "catalog.source",
        id=source.id,
        org_id=org,
        data_space_id=space,
        name="Kho",
        kind="postgresql",
        secret_ref=ref,
    )
    seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        source_id=source.id,
        origin="source",
        schema_name="dw",
        table_name="loan_balance",
        history_kind="none",
        enabled=True,
    )
    return org, space, user, source, secrets


async def test_a_query_runs_in_place_and_is_recorded(
    seed: Seed, warehouse: dict[str, object]
) -> None:
    org, space, user, source, secrets = _source(seed, warehouse)
    out = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="ad_hoc",
        caller_id=user,
        source=source,
        secrets=secrets,
        sql="SELECT branch_code, balance FROM loan_balance ORDER BY balance DESC",
    )
    assert out.ok, out.error
    assert [c["name"] for c in out.columns] == ["branch_code", "balance"]
    assert out.rows[0] == ["HCM", "2300.25"]  # amounts keep their precision as strings
    assert seed.one(
        "SELECT status, row_count, result_hash IS NOT NULL FROM query.query_run WHERE id = %s",
        out.query_run_id,
    ) == ("succeeded", 3, True)
    assert (
        seed.one(
            "SELECT jsonb_array_length(rows) FROM query.query_result WHERE query_run_id = %s",
            out.query_run_id,
        )
        == 3
    )


async def test_a_write_never_reaches_the_source(seed: Seed, warehouse: dict[str, object]) -> None:
    org, space, user, source, secrets = _source(seed, warehouse)
    out = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="ad_hoc",
        caller_id=user,
        source=source,
        secrets=secrets,
        sql="DELETE FROM loan_balance",
    )
    assert not out.ok and "allowed" in (out.error or "")
    assert seed.one("SELECT count(*) FROM dw.loan_balance") == 3
    assert (
        seed.one("SELECT status FROM query.query_run WHERE id = %s", out.query_run_id) == "failed"
    )


async def test_a_slow_query_is_stopped_at_the_source_timeout(
    seed: Seed, warehouse: dict[str, object]
) -> None:
    org, space, user, source, secrets = _source(seed, warehouse, timeout=1)
    out = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="ad_hoc",
        caller_id=user,
        source=source,
        secrets=secrets,
        # A cross join large enough to outlast one second, still a plain SELECT.
        sql="SELECT count(*) FROM generate_series(1, 200000000)",
    )
    assert out.status == "timeout"
    assert (
        seed.one("SELECT status FROM query.query_run WHERE id = %s", out.query_run_id) == "timeout"
    )


async def test_credentials_of_another_organisation_are_refused(
    seed: Seed, warehouse: dict[str, object]
) -> None:
    org, space, user, source, secrets = _source(seed, warehouse)
    source.secret_ref = f"secret/tenants/{uuid.uuid4()}/sources/dw"
    out = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="ad_hoc",
        caller_id=user,
        source=source,
        secrets=secrets,
        sql="SELECT 1",
    )
    assert not out.ok and "organisation" in (out.error or "")


async def test_a_table_outside_the_data_space_is_refused_before_the_source(
    seed: Seed, warehouse: dict[str, object]
) -> None:
    # The source login can read pg_catalog; the Data Space does not hold it.
    org, space, user, source, secrets = _source(seed, warehouse)
    out = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=user,
        caller_kind="ad_hoc",
        caller_id=user,
        source=source,
        secrets=secrets,
        sql="SELECT rolname FROM pg_catalog.pg_roles",
    )
    assert not out.ok and "not a dataset of this Data Space" in (out.error or "")
    assert seed.one(
        "SELECT status, source_id::text FROM query.query_run WHERE id = %s", out.query_run_id
    ) == ("failed", str(source.id))
