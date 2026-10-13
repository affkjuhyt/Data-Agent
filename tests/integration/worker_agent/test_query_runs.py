"""query_run.requested: worker-agent runs what the api recorded `queued` (the metric explorer, a
dataset with filters, the caller's SQL) on the bank schema of the answer tests, and refuses what
reads outside the Data Space or under an access policy it cannot apply yet."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import query_runs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

warehouse = test_answers.warehouse

pytestmark = [pytest.mark.integration]


def _queue(bank: Bank, caller_kind: str, payload: dict[str, Any], **run: Any) -> uuid.UUID:
    run_id = bank.seed.row(
        "query.query_run",
        org_id=bank.org,
        data_space_id=bank.space,
        caller_kind=caller_kind,
        caller_id=bank.user,
        principal_id=bank.user,
        route="live",
        sql_generated=run.pop("sql", ""),
        sql_executed="",
        status="queued",
        **run,
    )
    bank.seed.row(
        "query.outbox",
        org_id=bank.org,
        aggregate_type="query_run",
        aggregate_id=run_id,
        event_type=query_runs.EVENT_TYPE,
        payload=json.dumps(
            {"query_run_id": str(run_id), "principal_id": str(bank.user), **payload}, default=str
        ),
    )
    return run_id


async def _drain() -> None:
    source = OutboxTableSource(schema="query", event_types=frozenset(query_runs.HANDLERS))
    while await run_once(source, query_runs.HANDLERS, batch=50):
        pass


def _outcome(seed: Seed, run_id: uuid.UUID) -> tuple[Any, ...]:
    return seed.one("SELECT status, error FROM query.query_run WHERE id = %s", run_id)  # type: ignore[return-value]


def _rows(seed: Seed, run_id: uuid.UUID) -> list[list[Any]]:
    return seed.one("SELECT rows FROM query.query_result WHERE query_run_id = %s", run_id)  # type: ignore[return-value]


def _dimension(seed: Seed, bank: Bank, code: str) -> uuid.UUID:
    return seed.one(  # type: ignore[return-value]
        "SELECT id FROM context.semantic_dimension WHERE org_id = %s AND code = %s", bank.org, code
    )


def _metric(seed: Seed, bank: Bank, code: str) -> uuid.UUID:
    return seed.one(  # type: ignore[return-value]
        "SELECT id FROM context.metric WHERE org_id = %s AND code = %s", bank.org, code
    )


async def test_uc_01_04_the_explorer_compiles_each_metric_into_one_long_result(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = _queue(
        bank,
        "question",
        {
            "metric_ids": [bank.npl, _metric(seed, bank, "DU_NO")],
            "dimensions": [_dimension(seed, bank, "vung")],
            "filters": [],
        },
    )
    await _drain()
    assert _outcome(seed, run) == ("succeeded", None)
    rows = sorted(_rows(seed, run), key=lambda r: (r[0], r[1]))
    assert [(r[0], r[1]) for r in rows] == [
        ("DU_NO", "Miền Bắc"),
        ("DU_NO", "Miền Nam"),
        ("NPL_RATIO", "Miền Bắc"),
        ("NPL_RATIO", "Miền Nam"),
    ]
    assert seed.one(
        "SELECT cardinality(metric_version_ids), source_id IS NOT NULL FROM query.query_run"
        " WHERE id = %s",
        run,
    ) == (2, True)
    # Redelivered, the run is already taken: nothing runs twice.
    seed.one("UPDATE query.outbox SET published_at = NULL WHERE aggregate_id = %s RETURNING 1", run)
    await _drain()
    assert seed.one("SELECT count(*) FROM query.query_result WHERE query_run_id = %s", run) == 1


async def test_a_dataset_run_applies_its_filters_and_limit(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = _queue(
        bank,
        "ad_hoc",
        {
            "mode": "dataset",
            "dataset_id": bank.loans,
            "filters": [
                {"column": "branch_code", "op": "eq", "value": "HN1"},
                {"column": "group_code", "op": "in", "value": [1, 3]},
            ],
            "limit": 2,
        },
    )
    await _drain()
    assert _outcome(seed, run) == ("succeeded", None)
    rows = _rows(seed, run)
    assert len(rows) == 2 and {r[0] for r in rows} == {"HN1"}
    assert seed.one("SELECT truncated FROM query.query_result WHERE query_run_id = %s", run)


async def test_sql_finds_its_source_from_the_tables_it_reads(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = _queue(
        bank,
        "ad_hoc",
        {"mode": "sql", "limit": 100},
        sql="SELECT b.region_name, count(*) AS n FROM loan l JOIN bank.branch b"
        " ON b.code = l.branch_code GROUP BY b.region_name",
    )
    await _drain()
    assert _outcome(seed, run) == ("succeeded", None)
    assert sorted(tuple(r) for r in _rows(seed, run)) == [("Miền Bắc", 4), ("Miền Nam", 3)]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT rolname FROM pg_catalog.pg_roles",
        "SELECT 1",
    ],
)
async def test_sql_outside_the_data_space_never_runs(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = _queue(bank, "ad_hoc", {"mode": "sql", "limit": 100}, sql=sql)
    await _drain()
    status, error = _outcome(seed, run)
    assert status == "failed" and "Data Space" in error


async def test_br_45_a_dataset_under_an_active_policy_is_refused_not_run_unfiltered(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Theo chi nhánh",
        kind="row",
        condition_expr="branch_code = :user.branch",
        status="active",
        simulated_at="2026-10-01T00:00:00Z",
    )
    run = _queue(bank, "ad_hoc", {"mode": "dataset", "dataset_id": bank.loans, "limit": 10})
    await _drain()
    status, error = _outcome(seed, run)
    assert status == "failed" and "BR-45" in error
    assert seed.one("SELECT count(*) FROM query.query_result WHERE query_run_id = %s", run) == 0


async def test_a_past_as_of_fails_instead_of_running_live(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = _queue(
        bank,
        "ad_hoc",
        {"mode": "dataset", "dataset_id": bank.loans, "limit": 10},
        as_of=datetime.date(2026, 1, 31),
    )
    await _drain()
    assert _outcome(seed, run) == ("failed", query_runs.HISTORY)
