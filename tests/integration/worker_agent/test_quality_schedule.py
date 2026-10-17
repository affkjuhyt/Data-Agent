"""Quality rules checked on the data (UC-06-07) and the clock of the data side (UC-04-05/06,
UC-06-03): due loads and pipelines fire once per minute, a running pipeline is not overlapped."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import quality, scheduler
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


async def drain() -> None:
    source = OutboxTableSource(schema="ingest", event_types=frozenset(quality.HANDLERS))
    while await run_once(source, quality.HANDLERS, batch=20):
        pass


def rule(
    bank: Bank, kind: str, column: str | None, params: dict[str, Any], severity: str
) -> uuid.UUID:
    rid = bank.seed.row(
        "ingest.quality_rule",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        column_name=column,
        kind=kind,
        params=json.dumps(params),
        severity=severity,
        created_by=bank.user,
    )
    bank.seed.row(
        "ingest.outbox",
        org_id=bank.org,
        aggregate_type="quality_rule",
        aggregate_id=rid,
        event_type="quality_rule.created",
        payload=json.dumps({"quality_rule_id": str(rid)}),
    )
    return rid


@pytest.mark.req("AC-06-07-01")
async def test_rules_count_their_violations_on_the_data(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ids = {
        "not_null": rule(bank, "not_null", "product_code", {}, "medium"),
        "range": rule(bank, "range", "balance", {"max": 4000}, "high"),
        "unique": rule(bank, "unique", "branch_code", {}, "low"),
        "in_list": rule(bank, "in_list", "group_code", {"values": [1, 3, 4]}, "medium"),
        "custom": rule(
            bank, "custom_sql", None, {"sql": "SELECT * FROM loan WHERE balance < 0"}, "medium"
        ),
    }
    await drain()
    got = {
        name: seed.one("SELECT violations FROM ingest.quality_result WHERE rule_id = %s", rid)
        for name, rid in ids.items()
    }
    # 7 loans: one 5000 over the max, branches HN1 x3, HN2, SG1 x3 (4 repeats), one group 5.
    assert got == {"not_null": 0, "range": 1, "unique": 4, "in_list": 1, "custom": 0}
    # A failing high-severity rule blocks the table (BR-116).
    assert (
        seed.one("SELECT quality_state FROM catalog.dataset WHERE id = %s", bank.loans) == "blocked"
    )


async def test_a_rule_that_cannot_run_fails_closed(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    # Reads a table outside the Data Space: the executor refuses, the check does not pass.
    rid = rule(bank, "custom_sql", None, {"sql": "SELECT * FROM pg_catalog.pg_roles"}, "high")
    await drain()
    passed, ref = seed.one(
        "SELECT passed, sample_ref FROM ingest.quality_result WHERE rule_id = %s", rid
    )
    # A fixed category is stored, never the refusal's text (it can name what is out of reach).
    assert passed is False and ref == "error: query did not run"
    assert (
        seed.one("SELECT quality_state FROM catalog.dataset WHERE id = %s", bank.loans) == "blocked"
    )


async def test_a_rule_runs_with_its_authors_rights(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    rid = rule(bank, "not_null", "product_code", {}, "low")
    await drain()
    run_by = seed.one(
        "SELECT q.principal_id FROM query.query_run q JOIN ingest.quality_result r"
        " ON r.sample_ref = q.id::text WHERE r.rule_id = %s",
        rid,
    )
    assert str(run_by) == str(bank.user)


def job(bank: Bank, cron: str) -> uuid.UUID:
    source = bank.seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", bank.loans)
    jid = bank.seed.row(
        "ingest.ingestion_job",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        name="Nạp khoản vay",
        mode="full",
        source_object="loan",
        schedule_cron=cron,
        created_by=bank.user,
    )
    # The api emitted this when the job was created: the scheduler finds the org from it.
    bank.seed.row(
        "ingest.outbox",
        org_id=bank.org,
        aggregate_type="ingestion_job",
        aggregate_id=jid,
        event_type="ingestion_job.created",
        payload=json.dumps({"job_id": str(jid)}),
        published_at="2026-10-01T00:00:00Z",
    )
    return jid


@pytest.mark.req("AC-04-05-01")
async def test_a_due_job_is_loaded_once_per_minute(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    due, later = job(bank, "* * * * *"), job(bank, "0 3 * * *")
    at = datetime.datetime.now(datetime.UTC).replace(hour=12, minute=1)
    await scheduler.tick(at)
    await scheduler.tick(at)
    assert seed.one("SELECT count(*) FROM ingest.job_run WHERE job_id = %s", due) == 1
    assert seed.one("SELECT count(*) FROM ingest.job_run WHERE job_id = %s", later) == 0
    payload = seed.one(
        "SELECT payload FROM ingest.outbox WHERE event_type = 'job_run.backfill_requested'"
        " AND payload->>'job_id' = %s",
        str(due),
    )
    assert payload["scheduled"] is True and payload["principal_id"] == str(bank.user)


@pytest.mark.req("AC-06-03-01")
async def test_a_due_pipeline_runs_and_a_running_one_is_not_overlapped(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = uuid.uuid4()
    with seed.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline.pipeline (id, org_id, data_space_id, name, owner_id,"
            " input_dataset_ids, steps, status, cron) VALUES (%s,%s,%s,'p',%s,%s::uuid[],'[]',"
            " 'active','* * * * *')",
            (str(pid), str(bank.org), str(bank.space), str(bank.user), [str(bank.loans)]),
        )
    seed.row(
        "pipeline.outbox",
        org_id=bank.org,
        aggregate_type="pipeline",
        aggregate_id=pid,
        event_type="pipeline.created",
        payload=json.dumps({}),
        published_at="2026-10-01T00:00:00Z",
    )
    now = datetime.datetime.now(datetime.UTC)
    await scheduler.tick(now)
    assert (
        seed.one(
            "SELECT count(*) FROM pipeline.pipeline_run WHERE pipeline_id = %s AND status = 'running'",
            pid,
        )
        == 1
    )
    # Next minute, the first run still going: the scheduled one is skipped, not stacked.
    await scheduler.tick(now + datetime.timedelta(minutes=1))
    assert (
        seed.one(
            "SELECT count(*) FROM pipeline.pipeline_run WHERE pipeline_id = %s"
            " AND status = 'skipped_overlap'",
            pid,
        )
        == 1
    )


async def test_a_rule_touched_by_someone_else_runs_under_both(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    admin = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-adm", username="adm", display_name="Adm"
    )
    rid = rule(bank, "not_null", "product_code", {}, "low")
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest.quality_rule SET updated_by = %s WHERE id = %s", (str(admin), str(rid))
        )
    await drain()
    principals = {
        str(r[0])
        for r in seed.all("SELECT principal_id FROM query.query_run WHERE caller_id = %s", rid)
    }
    assert principals == {str(bank.user), str(admin)}


@pytest.mark.req("AC-06-07-03")
async def test_a_failing_high_rule_asks_the_steward_once(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.worker_agent import hitl

    bank = build(seed, warehouse, monkeypatch)
    rid = rule(bank, "range", "balance", {"max": 4000}, "high")
    await drain()
    # Checked again (an edit, the daily pass): still failing, not asked a second time.
    await quality.evaluate(bank.org, rid, run_kind="scheduled", run_ref=None)
    source = OutboxTableSource(schema="ingest", event_types=frozenset({hitl.EVENT_TYPE}))
    while await run_once(source, {hitl.EVENT_TYPE: hitl.open_request}, batch=20):
        pass
    asked = seed.one("SELECT count(*) FROM inbox.hitl_request WHERE source_ref = %s", rid)
    kind, service, assignee, title = seed.one(
        "SELECT kind, source_service, assignee_id, title FROM inbox.hitl_request"
        " WHERE source_ref = %s",
        rid,
    )
    steward = seed.one("SELECT steward_id FROM org.data_space WHERE id = %s", bank.space)
    assert asked == 1
    assert (kind, service, assignee) == ("data_review", "ingest", steward)
    assert "vi phạm 1 dòng" in title


async def test_an_edited_rule_is_checked_again_while_active(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    rid = rule(bank, "range", "balance", {"max": 4000}, "medium")
    await drain()

    def edited(status: str) -> None:
        with seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE ingest.quality_rule SET params = %s, status = %s WHERE id = %s",
                (json.dumps({"max": 6000}), status, str(rid)),
            )
        seed.row(
            "ingest.outbox",
            org_id=bank.org,
            aggregate_type="quality_rule",
            aggregate_id=rid,
            event_type=quality.UPDATED,
            payload=json.dumps({"quality_rule_id": str(rid)}),
        )

    edited("active")
    await drain()
    results = seed.one(
        "SELECT array_agg(passed ORDER BY checked_at) FROM ingest.quality_result WHERE rule_id = %s",
        rid,
    )
    assert results == [False, True]
    edited("disabled")
    await drain()
    assert seed.one("SELECT count(*) FROM ingest.quality_result WHERE rule_id = %s", rid) == 2
