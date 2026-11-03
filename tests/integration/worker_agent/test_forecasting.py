"""worker-agent trains a queued forecast job (UC-15-02, UC-15-03): the metric's own series read
through the compiler and the executor as the metric owner, as of the job's cutoff (BR-157); the
fit (ml-forecast, stubbed here: this image has no statsforecast); the version, its backtest and
its experimental predictions written as svc_forecast; and the version submitted to the owner the
way POST …versions/{id}:submit does (BR-158)."""

from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import psycopg2
import pytest

from libs.forecast_fit import Candidate, FitResult, Forecast, Point
from services.worker_agent import forecasting, storage
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]

PASSWORD = "sales-reader"
CUTOFF = "2026-07-15T00:00:00Z"
# 36 complete months before the cutoff's month: 2023-07 … 2026-06.
FIRST = dt.date(2023, 7, 1)
MONTHS = 36
REVISED = dt.date(2025, 1, 1)


def _month(i: int) -> dt.date:
    y, m = divmod(FIRST.month - 1 + i, 12)
    return dt.date(FIRST.year + y, m + 1, 1)


def _amount(i: int) -> float:
    return round(1000 + 10 * i + 150 * math.sin(2 * math.pi * _month(i).month / 12), 2)


@pytest.fixture(scope="module")
def warehouse(ddl_server: dict[str, str]) -> dict[str, Any]:
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS sales CASCADE")
        cur.execute("CREATE SCHEMA sales")
        cur.execute(
            "CREATE TABLE sales.revenue (region text, amount numeric, month_start date, "
            "available_at timestamptz)"
        )
        rows = []
        for i in range(MONTHS):
            month = _month(i)
            # Known two days after the month ends.
            known = (month + dt.timedelta(days=32)).replace(day=3)
            rows.append(("N", _amount(i), month, known))
        # A revision of 2025-01 recorded after the cutoff: not in the series as of the cutoff.
        rows.append(("N", 5000, REVISED, dt.datetime(2026, 8, 1, tzinfo=dt.UTC)))
        # July 2026 is the cutoff's own month, incomplete at the cutoff.
        rows.append(("N", 400, dt.date(2026, 7, 1), dt.datetime(2026, 7, 10, tzinfo=dt.UTC)))
        # A region the owner may not read (a row policy on the owner).
        rows.append(("HIDDEN", 7000, dt.date(2024, 3, 1), dt.datetime(2024, 4, 3, tzinfo=dt.UTC)))
        cur.executemany("INSERT INTO sales.revenue VALUES (%s, %s, %s, %s)", rows)
        # A stock read at each month's end: not additive, and 2025-03 is missing.
        cur.execute("CREATE TABLE sales.stock (qty numeric, month_start date)")
        cur.executemany(
            "INSERT INTO sales.stock VALUES (%s, %s)",
            [(_amount(i), _month(i)) for i in range(MONTHS) if _month(i) != dt.date(2025, 3, 1)],
        )
        # Twelve months only.
        cur.execute("CREATE TABLE sales.young (amount numeric, month_start date)")
        cur.executemany(
            "INSERT INTO sales.young VALUES (%s, %s)",
            [(_amount(i), _month(i)) for i in range(MONTHS - 12, MONTHS)],
        )
        # The books stop a few days into June 2026: its figure is a sliver of a month.
        cur.execute("CREATE TABLE sales.cut (amount numeric, month_start date)")
        cur.executemany(
            "INSERT INTO sales.cut VALUES (%s, %s)",
            [(_amount(i), _month(i)) for i in range(MONTHS - 1)] + [(5, _month(MONTHS - 1))],
        )
        cur.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_reader') "
            "THEN CREATE ROLE sales_reader LOGIN; END IF; END $$"
        )
        cur.execute("ALTER ROLE sales_reader PASSWORD %s", (PASSWORD,))
        cur.execute("GRANT USAGE ON SCHEMA sales TO sales_reader")
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA sales TO sales_reader")
    conn.close()
    parts = urlsplit(dsn)
    return {"host": parts.hostname, "port": parts.port, "database": parts.path.lstrip("/")}


@dataclass
class Shop:
    org: uuid.UUID
    space: uuid.UUID
    analyst: uuid.UUID
    owner: uuid.UUID
    revenue: uuid.UUID
    seed: Seed
    models: dict[str, uuid.UUID] = field(default_factory=dict)

    def job(self, metric: str, *, horizon: int = 3, status: str = "queued") -> uuid.UUID:
        job = self.seed.row(
            "forecast.forecast_job",
            org_id=self.org,
            data_space_id=self.space,
            requested_by=self.analyst,
            metric_code=metric,
            frequency="month",
            horizon=horizon,
            cutoff_at=CUTOFF,
            status=status,
            progress=json.dumps({"step": status, "purpose": "forecast"}),
            created_by=self.analyst,
        )
        self.seed.row(
            "forecast.outbox",
            org_id=self.org,
            aggregate_type="forecast_job",
            aggregate_id=job,
            event_type="forecast.job.state_changed",
            payload=json.dumps({"job_id": str(job), "previous_status": None, "status": "queued"}),
        )
        return job


def build(seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Shop:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Bán lẻ thử")
    analyst = seed.row(
        "org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A"
    )
    owner = seed.row("org.app_user", org_id=org, external_id="kc-o", username="o", display_name="O")
    steward = seed.row(
        "org.app_user", org_id=org, external_id="kc-s", username="s", display_name="S"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-BL", name="Bán lẻ", steward_id=steward)
    role = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=owner,
        role_id=role,
    )
    ref = f"secret/tenants/{org}/sources/sales"
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Kho bán lẻ",
        kind="postgresql",
        host=warehouse["host"],
        port=warehouse["port"],
        database_name=warehouse["database"],
        conn_options=json.dumps({"schema": "sales", "sslmode": "disable"}),
        secret_ref=ref,
        mode="federated",
        status="active",
    )
    from services.worker_agent import questions

    monkeypatch.setattr(
        questions, "_secrets", StaticReader({ref: {"user": "sales_reader", "password": PASSWORD}})
    )
    shop = Shop(org, space, analyst, owner, uuid.uuid4(), seed)

    def metric(
        table: str,
        code: str,
        *,
        measure: str,
        additivity: str,
        semantics: str,
        temporal: bool = False,
    ) -> uuid.UUID:
        dataset = seed.row(
            "catalog.dataset",
            org_id=org,
            data_space_id=space,
            source_id=source,
            origin="source",
            schema_name="sales",
            table_name=table,
            history_kind="temporal" if temporal else "none",
            knowledge_time_col="available_at" if temporal else None,
            enabled=True,
        )
        entity = seed.row(
            "context.semantic_entity",
            org_id=org,
            data_space_id=space,
            code=f"e_{table}",
            name=table,
            primary_dataset_id=dataset,
            key_columns=["month_start"],
        )
        seed.row(
            "context.semantic_measure",
            org_id=org,
            data_space_id=space,
            entity_id=entity,
            code=f"m_{table}",
            name=table,
            expression=measure,
            aggregation="sum",
            additivity=additivity,
        )
        seed.row(
            "context.semantic_dimension",
            org_id=org,
            data_space_id=space,
            entity_id=entity,
            code=f"thang_{table}",
            name="tháng",
            expression="month_start",
            is_time=True,
        )
        m = seed.row(
            "context.metric",
            org_id=org,
            data_space_id=space,
            code=code,
            owner_id=owner,
            current_version=1,
            status="active",
        )
        seed.row(
            "context.metric_version",
            org_id=org,
            metric_id=m,
            version=1,
            display_name=code,
            expression=f"m_{table}",
            unit="triệu đồng",
            period_semantics=semantics,
            time_grain="month",
            status="certified",
            certified_by=owner,
            certified_at="2026-01-01T00:00:00Z",
            valid_from="2026-01-01T00:00:00Z",
        )
        shop.models[code] = seed.row(
            "forecast.forecast_model",
            org_id=org,
            data_space_id=space,
            metric_code=code,
            frequency="month",
            owner_id=owner,
            created_by=analyst,
        )
        return dataset

    shop.revenue = metric(
        "revenue",
        "DOANH_THU",
        measure="SUM(amount)",
        additivity="full",
        semantics="sum_over_period",
        temporal=True,
    )
    metric("stock", "TON_KHO", measure="SUM(qty)", additivity="semi", semantics="end_of_period")
    metric(
        "young",
        "DOANH_THU_MOI",
        measure="SUM(amount)",
        additivity="full",
        semantics="sum_over_period",
    )
    metric(
        "cut",
        "DOANH_THU_CAT",
        measure="SUM(amount)",
        additivity="full",
        semantics="sum_over_period",
    )
    # The owner reads no row of the HIDDEN region.
    seed.row(
        "governance.access_policy",
        org_id=org,
        data_space_id=space,
        dataset_id=shop.revenue,
        name="Ẩn vùng thử",
        kind="row",
        applies_to=json.dumps({"user_ids": [str(owner)]}),
        condition_expr="region <> 'HIDDEN'",
        status="active",
        simulated_at="2026-06-01T08:00:00Z",
        activated_at="2026-06-01T09:00:00Z",
        activated_by=steward,
    )
    return shop


CANDIDATES = [
    Candidate("naive", 1.31, 14.2, 0.55, 0.81, 1.02),
    Candidate("seasonal_naive", 1.0, 9.8, 0.7, 0.9, 1.0),
    Candidate("ets", 0.62, 4.1, 0.83, 0.96, 0.58),
    Candidate("theta", 0.74, 5.0, 0.8, 0.94, 0.66),
    Candidate("arima", None, None, None, None, None),
]


def realistic(series: list[Point], horizon: int) -> FitResult:
    """A fit as ml-forecast answers it: ets chosen, six rolling cutoffs, ordered intervals."""
    last = series[-1].period
    periods = []
    for _ in range(horizon):
        last = (last + dt.timedelta(days=32)).replace(day=1)
        periods.append(last)
    cutoffs = [series[-1 - horizon - k].period for k in range(6)][::-1]
    return FitResult(
        chosen="ets",
        reason=None,
        candidates=CANDIDATES,
        cutoffs=cutoffs,
        predictions=[
            Forecast(p, 1400.0 + i, 1300.0 + i, 1500.0 + i, 1250.0 + i, 1560.0 + i)
            for i, p in enumerate(periods)
        ],
        interval_method="model",
        params={"ets": {"model": "AAA", "season_length": 12}},
    )


class Trainer:
    def __init__(self, answer: Any = realistic) -> None:
        self.answer = answer
        self.calls: list[tuple[list[Point], str, int]] = []

    async def __call__(self, series: list[Point], *, frequency: str, horizon: int) -> FitResult:
        self.calls.append((series, frequency, horizon))
        if isinstance(self.answer, Exception):
            raise self.answer
        result: FitResult = self.answer(series, horizon)
        return result


class Store:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        self.objects[(bucket, key)] = body


@pytest.fixture
def trainer(monkeypatch: pytest.MonkeyPatch) -> Trainer:
    fake = Trainer()
    monkeypatch.setattr(forecasting, "fit_series", fake)
    return fake


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    fake = Store()
    monkeypatch.setattr(storage, "_store", fake)
    return fake


async def drain() -> None:
    source = OutboxTableSource(schema="forecast", event_types=frozenset(forecasting.HANDLERS))
    while await run_once(source, forecasting.HANDLERS, batch=10):
        pass


def job_row(seed: Seed, job: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, progress, model_version_id, finished_at FROM forecast.forecast_job "
        "WHERE id = %s",
        job,
    )


def job_events(seed: Seed, job: uuid.UUID) -> list[tuple[Any, Any]]:
    return [
        (r[0].get("previous_status"), r[0]["status"])
        for r in seed.all(
            "SELECT payload FROM forecast.outbox WHERE aggregate_id = %s "
            "AND event_type = 'forecast.job.state_changed' ORDER BY created_at, id",
            job,
        )
    ]


def versions(seed: Seed, shop: Shop, code: str) -> list[tuple[Any, ...]]:
    return seed.all(
        "SELECT id, status, algorithm FROM forecast.model_version WHERE model_id = %s",
        shop.models[code],
    )


async def test_a_queued_job_trains_backtests_and_submits_a_version(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    trainer: Trainer,
    store: Store,
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU", horizon=3)
    await drain()

    status, progress, version_id, finished = job_row(seed, job)
    assert status == "ready", progress
    assert finished is not None
    assert job_events(seed, job) == [
        (None, "queued"),
        ("queued", "preparing"),
        ("preparing", "training"),
        ("training", "backtesting"),
        ("backtesting", "ready"),
    ]
    assert len(trainer.calls) == 1
    series, frequency, horizon = trainer.calls[0]
    assert (frequency, horizon) == ("month", 3)
    fit = realistic(series, horizon)

    (only,) = versions(seed, shop, "DOANH_THU")
    assert only[0] == version_id
    v = seed.one(
        "SELECT status, algorithm, params, training_from, training_to, n_points, "
        "data_snapshot_refs, model_card, artifact_key, created_by, backtest_id, version "
        "FROM forecast.model_version WHERE id = %s",
        version_id,
    )
    (
        v_status,
        algorithm,
        params,
        training_from,
        training_to,
        n_points,
        refs,
        card,
        artifact_key,
        created_by,
        backtest_id,
        number,
    ) = v  # type: ignore[misc]
    # Written draft, then submitted: pending_approval, with the submit note of :submit.
    assert v_status == "pending_approval"
    assert algorithm == fit.chosen
    assert algorithm in {c.algorithm for c in CANDIDATES}
    assert params == fit.params
    assert (training_from, training_to, n_points) == (FIRST, dt.date(2026, 6, 1), MONTHS)
    assert number == 1
    assert created_by == shop.analyst
    assert [r["action"] for r in card["review"]] == ["submit"]
    assert len(card["scope_policy_hash"]) == 64
    assert card["limits"]
    assert {row["algorithm"] for row in card["backtest"]} == {c.algorithm for c in CANDIDATES}
    # The query run the series was read by is the version's snapshot.
    (run_id,) = refs
    assert seed.one("SELECT principal_id FROM query.query_run WHERE id = %s", run_id) == shop.owner

    # The params also as the version's artifact.
    assert artifact_key == f"forecast/{shop.org}/{version_id}.json"
    assert json.loads(store.objects[("da-forecast-models", artifact_key)]) == fit.params

    b = seed.one(
        "SELECT id, frequency, cutoffs, horizon, metrics, baseline_metrics, mase, in_sample_mase, "
        "candidates, finished_at FROM forecast.backtest_run WHERE model_version_id = %s",
        version_id,
    )
    b_id, b_freq, cutoffs, b_horizon, metrics, baseline, mase, in_sample, cands, b_done = b  # type: ignore[misc]
    assert b_id == backtest_id
    assert (b_freq, b_horizon) == ("month", 3)
    assert len(cutoffs) >= 6 and cutoffs == fit.cutoffs
    assert float(mase) == pytest.approx(0.62) and float(in_sample) == pytest.approx(0.58)
    assert metrics["mase"] == pytest.approx(0.62)
    assert metrics["interval_method"] == "parametric"
    assert baseline["mase"] == pytest.approx(1.0)
    assert [c["algorithm"] for c in cands] == [c.algorithm for c in CANDIDATES]
    # sMAPE kept as a share, as the screens read it.
    assert next(c for c in cands if c["algorithm"] == "ets")["smape"] == pytest.approx(0.041)
    assert b_done is not None

    predictions = seed.all(
        "SELECT period, point, lo80, hi80, lo95, hi95, is_experimental, interval_method, cutoff_at "
        "FROM forecast.prediction WHERE model_version_id = %s AND job_id = %s ORDER BY period",
        version_id,
        job,
    )
    assert len(predictions) == 3
    assert [p[0] for p in predictions] == [f.period for f in fit.predictions]
    for p, f in zip(predictions, fit.predictions, strict=True):
        assert float(p[1]) == pytest.approx(f.point)
        assert p[4] <= p[2] <= p[1] <= p[3] <= p[5]
        assert p[6] is True
        assert p[7] == "parametric"
        assert p[8] == dt.datetime(2026, 7, 15, tzinfo=dt.UTC)

    submitted = seed.all(
        "SELECT payload FROM forecast.outbox WHERE aggregate_id = %s "
        "AND event_type = 'forecast.model_version.submitted'",
        version_id,
    )
    assert len(submitted) == 1
    payload = submitted[0][0]
    assert payload["assignee_id"] == str(shop.owner)
    assert payload["hitl_kind"] == "forecast_model"
    assert payload["backtest_id"] == str(backtest_id)
    assert payload["model_id"] == str(shop.models["DOANH_THU"])


async def test_the_series_is_read_as_the_metric_owner_at_the_cutoff(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    trainer: Trainer,
    store: Store,
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU")
    await drain()
    assert job_row(seed, job)[0] == "ready"

    series, _, _ = trainer.calls[0]
    by_period = {p.period: p.value for p in series}
    # Complete months before the cutoff only: July 2026 is the cutoff's own month.
    assert min(by_period) == FIRST and max(by_period) == dt.date(2026, 6, 1)
    assert [p.period for p in series] == sorted(by_period)
    # The revision recorded after the cutoff is not in it (BR-157)…
    assert by_period[REVISED] == pytest.approx(_amount(18))
    # …nor the row the owner's policy hides (invariant 13).
    assert by_period[dt.date(2024, 3, 1)] == pytest.approx(_amount(8))
    (run_id,) = seed.one(
        "SELECT data_snapshot_refs FROM forecast.model_version WHERE id = %s",
        job_row(seed, job)[2],
    )  # type: ignore[misc]
    executed, principal, applied = seed.one(  # type: ignore[misc]
        "SELECT sql_executed, principal_id, cardinality(policies_applied) FROM query.query_run "
        "WHERE id = %s",
        run_id,
    )
    assert principal == shop.owner
    assert applied == 1
    assert "available_at" in executed and "HIDDEN" in executed


async def test_a_job_whose_candidates_lose_ends_failed_without_a_version(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, trainer: Trainer
) -> None:
    reason = "Không mô hình nào tốt hơn cách đoán theo mùa (seasonal naive)"
    trainer.answer = lambda series, horizon: FitResult(
        chosen=None,
        reason=reason,
        candidates=[Candidate("seasonal_naive", 1.0, 9.8, 0.7, 0.9, 1.0)],
        cutoffs=[series[-4 - k].period for k in range(6)],
        predictions=[],
        interval_method=None,
        params={},
    )
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU")
    await drain()
    status, progress, version_id, finished = job_row(seed, job)
    assert status == "failed"
    assert progress["error"] == reason
    assert version_id is None and finished is not None
    assert versions(seed, shop, "DOANH_THU") == []
    assert (
        seed.one(
            "SELECT count(*) FROM forecast.outbox WHERE org_id = %s "
            "AND event_type = 'forecast.model_version.submitted'",
            shop.org,
        )
        == 0
    )
    assert job_events(seed, job)[-1] == ("training", "failed")


async def test_a_gap_in_a_non_additive_series_fails_the_job_naming_the_period(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, trainer: Trainer
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("TON_KHO")
    await drain()
    status, progress, version_id, _ = job_row(seed, job)
    assert status == "failed"
    assert "2025-03" in progress["error"]
    assert version_id is None
    # Never filled with 0, never trained.
    assert trainer.calls == []
    assert versions(seed, shop, "TON_KHO") == []
    assert job_events(seed, job)[-1] == ("preparing", "failed")


async def test_too_short_a_series_fails_in_words(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, trainer: Trainer
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU_MOI")
    await drain()
    status, progress, _, _ = job_row(seed, job)
    assert status == "failed"
    assert progress["error"] == "Cần ít nhất 24 kỳ có dữ liệu; chuỗi này có 12"
    assert trainer.calls == []


async def test_the_trainer_down_fails_the_job_in_words(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real client against a port nothing listens on.
    monkeypatch.setenv("FORECAST_SERVICE_URL", "http://127.0.0.1:9")
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU")
    await drain()
    status, progress, version_id, _ = job_row(seed, job)
    assert status == "failed"
    assert progress["error"] == "Dịch vụ dự báo không phản hồi"
    assert version_id is None
    assert versions(seed, shop, "DOANH_THU") == []


async def test_a_job_already_past_queued_is_ignored(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    trainer: Trainer,
    store: Store,
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    # A replayed `queued` event of a job another worker already took.
    taken = shop.job("DOANH_THU", status="training")
    await drain()
    assert job_row(seed, taken)[0] == "training"
    assert job_events(seed, taken) == [(None, "queued")]
    assert trainer.calls == []

    # And a finished job's event, replayed, trains nothing twice.
    job = shop.job("DOANH_THU")
    await drain()
    assert job_row(seed, job)[0] == "ready"
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE forecast.outbox SET published_at = NULL WHERE aggregate_id = %s",
            (str(job),),
        )
    await drain()
    assert len(trainer.calls) == 1
    assert len(versions(seed, shop, "DOANH_THU")) == 1


async def test_a_partial_last_month_is_left_out_of_training(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    trainer: Trainer,
    store: Store,
) -> None:
    shop = build(seed, warehouse, monkeypatch)
    job = shop.job("DOANH_THU_CAT", horizon=3)
    await drain()
    status, progress, version_id, _ = job_row(seed, job)
    assert status == "ready", progress

    series, _, _ = trainer.calls[0]
    last_complete = _month(MONTHS - 2)
    assert series[-1].period == last_complete
    assert len(series) == MONTHS - 1
    training_to, n_points, card = seed.one(  # type: ignore[misc]
        "SELECT training_to, n_points, model_card FROM forecast.model_version WHERE id = %s",
        version_id,
    )
    assert (training_to, n_points) == (last_complete, MONTHS - 1)
    assert "Bỏ kỳ 2026-06 vì dữ liệu chưa đủ kỳ" in card["limits"]
    # The horizon starts after the last whole month, at the month that was left out.
    first = seed.one(
        "SELECT min(period) FROM forecast.prediction WHERE model_version_id = %s", version_id
    )
    assert first == _month(MONTHS - 1)
