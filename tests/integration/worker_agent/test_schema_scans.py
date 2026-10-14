"""UC-04-02 against a real PostgreSQL: the V3 DDL server stands in for the bank's database too,
with a read-only login of its own. Each test builds the bank's schema afresh."""

from __future__ import annotations

import datetime
import uuid
import zoneinfo
from typing import Any
from urllib.parse import urlsplit

import psycopg2
import pytest
from psycopg2.extras import Json

from services.worker_agent import schema_scans
from services.worker_agent.events import Event
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]

SCHEMA = "scan_uc0402"
READER, PASSWORD = "scan_reader", "scan-reader-secret"


def _bank_sql(cur: Any) -> None:
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    cur.execute(
        f"CREATE TABLE {SCHEMA}.customers (id integer PRIMARY KEY, ho_ten text, so_cccd text,"
        " lien_he text, email text, branch text)"
    )
    cur.execute(
        f"INSERT INTO {SCHEMA}.customers VALUES"
        " (1, 'Nguyễn An', '001203004005', '0901234567', 'an@bank.vn', 'HN'),"
        " (2, 'Trần Bình', '001203004006', '0912 345 678', 'binh@bank.vn', 'HN'),"
        " (3, 'Lê Chi', '001203004007', '0987.654.321', 'chi@bank.vn', NULL)"
    )
    # customer_id points at customers without a declared foreign key (step 5 infers it).
    cur.execute(
        f"CREATE TABLE {SCHEMA}.loans (id integer PRIMARY KEY, customer_id integer,"
        " amount numeric, active boolean, extra jsonb)"
    )
    cur.execute(
        f"INSERT INTO {SCHEMA}.loans VALUES (10, 1, 120.5, true, '{{}}'),"
        " (11, 2, NULL, false, NULL), (12, 2, 99, true, NULL)"
    )
    cur.execute(
        f"CREATE TABLE {SCHEMA}.payments (id integer PRIMARY KEY,"
        f" loan_id integer REFERENCES {SCHEMA}.loans(id), paid numeric)"
    )
    cur.execute(f"INSERT INTO {SCHEMA}.payments VALUES (100, 10, 20), (101, 11, 5)")
    cur.execute(
        f"CREATE TABLE {SCHEMA}.du_no_scd2 (id integer PRIMARY KEY, ma_kh text,"
        " valid_from date, valid_to date, du_no numeric)"
    )
    cur.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READER}')"
        f" THEN CREATE ROLE {READER} LOGIN; END IF; END $$"
    )
    cur.execute(f"ALTER ROLE {READER} PASSWORD %s", (PASSWORD,))
    cur.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {READER}")
    cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {READER}")


@pytest.fixture
def bank(ddl_server: dict[str, str]) -> dict[str, Any]:
    dsn = ddl_server["dataagent"]
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            _bank_sql(cur)
    parts = urlsplit(dsn)
    return {"dsn": dsn, "host": parts.hostname, "port": parts.port, "db": parts.path.lstrip("/")}


def _bank(bank: dict[str, Any], sql: str) -> None:
    with psycopg2.connect(bank["dsn"]) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)


class _Org:
    def __init__(self, seed: Seed, bank: dict[str, Any], **source: Any) -> None:
        self.seed = seed
        self.org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Scan")
        self.user = seed.row(
            "org.app_user", org_id=self.org, external_id=f"scan-{uuid.uuid4().hex[:6]}",
            username="scan", display_name="Scan",
        )  # fmt: skip
        self.space = seed.row(
            "org.data_space", org_id=self.org, code="DS-SCAN", name="Scan", steward_id=self.user
        )
        self.ref = f"secret/tenants/{self.org}/sources/bank"
        values: dict[str, Any] = {
            "org_id": self.org,
            "data_space_id": self.space,
            "name": "Bank",
            "kind": "postgresql",
            "host": bank["host"],
            "port": bank["port"],
            "database_name": bank["db"],
            "conn_options": Json({"sslmode": "disable"}),
            "secret_ref": self.ref,
            "scan_schemas": [SCHEMA],
            "status": "active",
        }
        values.update(source)
        self.source = seed.row("catalog.source", **values)

    async def run(self, kind: str, trigger: str = "manual", tables: list[str] | None = None) -> Any:
        run = self.seed.row(
            "catalog.scan_run", org_id=self.org, source_id=self.source, kind=kind, trigger=trigger
        )
        await self.handle(run, tables)
        return run

    async def handle(self, run: Any, tables: list[str] | None = None) -> None:
        await schema_scans.scan_schema(
            Event(
                event_id=uuid.uuid4(),
                org_id=self.org,
                event_type=schema_scans.EVENT_TYPE,
                aggregate_type="scan_run",
                aggregate_id=run,
                payload={
                    "scan_run_id": str(run),
                    "source_id": str(self.source),
                    "requested_by": str(self.user),
                    "tables": tables,
                },
            )
        )

    def dataset(self, table: str) -> Any:
        return self.seed.one(
            "SELECT id FROM catalog.dataset WHERE source_id = %s AND table_name = %s",
            self.source,
            table,
        )

    def column(self, table: str, column: str, what: str) -> Any:
        return self.seed.one(
            f"SELECT {what} FROM catalog.column_meta WHERE dataset_id = %s AND name = %s",
            self.dataset(table),
            column,
        )

    def profile(self, run: Any, table: str, column: str, what: str) -> Any:
        return self.seed.one(
            f"SELECT {what} FROM catalog.column_profile p JOIN catalog.column_meta c"
            " ON c.id = p.column_id WHERE p.scan_run_id = %s AND c.dataset_id = %s AND c.name = %s",
            run,
            self.dataset(table),
            column,
        )

    def queued_profile(self) -> Any:
        """The profile the source's first scan queued (step 3), still to run."""
        return self.seed.one(
            "SELECT id FROM catalog.scan_run WHERE source_id = %s AND kind = 'profile'"
            " AND trigger = 'initial' AND status = 'running'",
            self.source,
        )

    def scan(self, run: Any) -> Any:
        return self.seed.one(
            "SELECT status, tables_scanned, tables_skipped, resume_from FROM catalog.scan_run"
            " WHERE id = %s",
            run,
        )


@pytest.fixture
def org(seed: Seed, bank: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> _Org:
    made = _Org(seed, bank)
    reader = StaticReader({made.ref: {"user": READER, "password": PASSWORD}})
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)
    return made


@pytest.mark.req("AC-04-02-01")
async def test_the_first_scan_records_the_structure_then_its_profile(org: _Org) -> None:
    run = await org.run("schema", trigger="initial")
    assert org.scan(run)[:2] == ("succeeded", 4)
    for table in ("customers", "loans", "payments", "du_no_scd2"):
        dataset = org.dataset(table)
        assert dataset
        assert org.seed.one(
            "SELECT schema_hash IS NOT NULL FROM catalog.dataset_version WHERE dataset_id = %s",
            dataset,
        )
    assert org.column("customers", "id", "is_key") is True
    events = org.seed.one(
        "SELECT array_agg(event_type ORDER BY created_at) FROM catalog.outbox"
        " WHERE aggregate_id = %s",
        run,
    )
    assert events == ["scan.completed"]
    # Step 3 follows the first scan: its profile is queued as a scan of its own.
    profile = org.queued_profile()
    assert profile
    assert org.seed.one(
        "SELECT event_type FROM catalog.outbox WHERE aggregate_id = %s", profile
    ) == ("scan_run.created")
    await org.handle(profile)
    assert org.scan(profile)[:2] == ("succeeded", 4)
    sample_rows, null_ratio, distinct = org.profile(
        profile, "customers", "branch", "sample_rows, null_ratio, distinct_est"
    )
    assert (sample_rows, float(null_ratio), distinct) == (3, 0.33333, 1)
    assert org.profile(profile, "loans", "amount", "min_value, max_value") == ("99", "120.5")
    assert org.profile(profile, "customers", "branch", "top_values") == [
        {"value": "HN", "count": 2}
    ]


@pytest.mark.req("AC-04-02-02")
async def test_personal_columns_are_labelled_and_keep_no_values(org: _Org) -> None:
    await org.run("schema", trigger="initial")
    # By name, at the structure scan: the screen can ask for the masking to be confirmed.
    assert org.column("customers", "so_cccd", "pii_label") == "national_id"
    assert org.column("customers", "ho_ten", "pii_label") == "name"
    assert org.column("customers", "email", "pii_label") == "email"
    assert org.column("customers", "lien_he", "pii_label") is None
    profile = org.queued_profile()
    await org.handle(profile)
    # By its values, at the profile: lien_he holds phone numbers, whatever their spacing.
    assert org.column("customers", "lien_he", "pii_label") == "phone"
    assert org.profile(profile, "customers", "lien_he", "pattern_hint") == "số điện thoại di động"
    assert org.profile(profile, "customers", "so_cccd", "pattern_hint") == "số CCCD (12 chữ số)"
    for column in ("so_cccd", "lien_he", "email", "ho_ten"):
        top, low, high = org.profile(
            profile, "customers", column, "top_values, min_value, max_value"
        )
        assert (top, low, high) == ([], None, None), column
    # What a person decided is kept: lien_he confirmed as not personal stays so.
    org.seed.one(
        "UPDATE catalog.column_meta SET pii_label = NULL, pii_confirmed = true"
        " WHERE dataset_id = %s AND name = 'lien_he' RETURNING id",
        org.dataset("customers"),
    )
    await org.run("profile")
    assert org.column("customers", "lien_he", "pii_label") is None


@pytest.mark.req("AC-04-02-03")
async def test_a_history_table_records_its_kind_and_date(org: _Org) -> None:
    await org.run("schema", trigger="initial")
    dataset = org.dataset("du_no_scd2")
    assert org.seed.one(
        "SELECT history_kind, business_date_col FROM catalog.dataset WHERE id = %s", dataset
    ) == ("scd2", "valid_from")
    assert org.seed.one(
        "SELECT history_kind FROM catalog.dataset WHERE id = %s", org.dataset("loans")
    ) == ("none")


@pytest.mark.req("AC-04-02-04")
async def test_a_table_past_the_timeout_is_skipped_and_the_others_profiled(
    seed: Seed, bank: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _bank(bank, f"CREATE VIEW {SCHEMA}.slow_report AS SELECT 1 AS id, pg_sleep(6)::text AS waited")
    _bank(bank, f"GRANT SELECT ON {SCHEMA}.slow_report TO {READER}")
    org = _Org(seed, bank, query_timeout_s=5)
    reader = StaticReader({org.ref: {"user": READER, "password": PASSWORD}})
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)
    await org.run("schema")
    profile = await org.run("profile")
    status, scanned, skipped, resume = org.scan(profile)
    assert status == "partial" and resume is None
    assert skipped == [{"table": f"{SCHEMA}.slow_report", "reason": "timeout"}]
    assert scanned == 4
    assert org.profile(profile, "loans", "amount", "sample_rows") == 3


@pytest.mark.req("AC-04-02-05")
async def test_a_lost_connection_resumes_from_the_table_not_profiled(
    org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await org.run("schema")
    real = schema_scans._profile_table

    async def drops_at_loans(conn: Any, table: dict[str, Any], masked: set[str]) -> Any:
        if table["table_name"] == "loans":
            raise ConnectionResetError("the source went away")
        return await real(conn, table, masked)

    monkeypatch.setattr(schema_scans, "_profile_table", drops_at_loans)
    first = await org.run("profile")
    status, scanned, skipped, resume = org.scan(first)
    assert (status, resume, skipped) == ("partial", f"{SCHEMA}.loans", [])
    assert scanned == 2  # customers, du_no_scd2

    monkeypatch.setattr(schema_scans, "_profile_table", real)
    second = await org.run("profile")
    assert org.scan(second)[:2] == ("succeeded", 4)
    # It started from loans: customers was not read again.
    assert org.profile(second, "customers", "branch", "count(*)") == 0
    assert org.profile(second, "loans", "amount", "count(*)") == 1


@pytest.mark.req("AC-04-02-06")
async def test_a_profile_waits_for_the_window_outside_peak_hours(
    seed: Seed, bank: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh"))
    start = (now + datetime.timedelta(hours=2)).strftime("%H:%M")
    end = (now + datetime.timedelta(hours=3)).strftime("%H:%M")
    org = _Org(seed, bank, scan_window=Json({"start": start, "end": end}))
    reader = StaticReader({org.ref: {"user": READER, "password": PASSWORD}})
    monkeypatch.setattr(schema_scans, "secrets", lambda: reader)
    await org.run("schema")
    profile = await org.run("profile")
    status, scanned, _, resume = org.scan(profile)
    assert (status, scanned, resume) == ("partial", 0, f"{SCHEMA}.customers")
    assert org.seed.one(
        "SELECT count(*) FROM catalog.column_profile WHERE scan_run_id = %s", profile
    ) == (0)


@pytest.mark.req("AC-04-02-06")
async def test_a_large_table_is_read_through_a_sample(
    org: _Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    await org.run("schema")
    # As if every table were above BR-15's limit: the profile reads it through TABLESAMPLE.
    monkeypatch.setattr(schema_scans, "sample_percent", lambda rows: 100.0)
    profile = await org.run("profile", tables=[f"{SCHEMA}.loans"])
    assert org.scan(profile)[0] == "succeeded"
    assert org.profile(profile, "loans", "id", "sample_rows") == 3


@pytest.mark.req("AC-04-02-07")
async def test_declared_and_inferred_joins_await_review(org: _Org) -> None:
    await org.run("schema", trigger="initial")
    declared = org.seed.one(
        "SELECT origin, status, from_columns, to_columns FROM context.relationship"
        " WHERE from_dataset_id = %s AND to_dataset_id = %s",
        org.dataset("payments"),
        org.dataset("loans"),
    )
    assert declared == ("declared", "pending_review", ["loan_id"], ["id"])
    await org.handle(org.queued_profile())
    inferred = org.seed.one(
        "SELECT origin, status, from_columns, to_columns, overlap_ratio FROM context.relationship"
        " WHERE from_dataset_id = %s AND to_dataset_id = %s",
        org.dataset("loans"),
        org.dataset("customers"),
    )
    assert inferred[:4] == ("inferred", "pending_review", ["customer_id"], ["id"])
    assert float(inferred[4]) == 1.0


async def test_a_changed_table_is_left_for_uc_04_03(org: _Org, bank: dict[str, Any]) -> None:
    await org.run("schema", trigger="initial")
    _bank(bank, f"ALTER TABLE {SCHEMA}.customers ADD COLUMN ghi_chu text")
    again = await org.run("schema")
    status, scanned, skipped, _ = org.scan(again)
    assert status == "succeeded" and scanned == 3
    assert [(s["table"], s["reason"]) for s in skipped] == [
        (f"{SCHEMA}.customers", "schema_changed")
    ]
    assert skipped[0]["schema_hash"]
    # The version in use is untouched; UC-04-03 decides on the change.
    assert org.column("customers", "ghi_chu", "count(*)") == 0
    # A second scan of a catalogued source does not queue another profile.
    assert (
        org.seed.one(
            "SELECT count(*) FROM catalog.scan_run WHERE source_id = %s AND kind = 'profile'",
            org.source,
        )
        == 1
    )
