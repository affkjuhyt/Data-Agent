"""Dự báo (UC-15-01 to UC-15-08) against PostgreSQL: forecast jobs, models and their versions,
scenarios, accuracy, formula forecasts and the forecast ledger.

Training, backtests and forecast runs are ml-forecast's job and no worker runs here: a request that
starts one is proved by its forecast_job row, its outbox event and the 202 with a Location. What
ml-forecast would have written (versions, backtests, predictions, ledger lines) is seeded as the
admin. Each test builds its own organisation: the metric owner who decides on versions, the builder
who made them, a second steward, an analyst who only asks and a user without the ask capability,
all members of one Data Space; a second Data Space holds a model of its own.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg2
import psycopg2.errors
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import PASSWORD, Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

TODAY = datetime.date.today()
NOW = datetime.datetime.now(datetime.UTC)
SCOPE = {"khoi": "ban_le"}
FEATURE = "lai_suat_dieu_hanh"
EXPRESSION = "(world_avg * fx_rate + env_tax) * (1 + vat_rate)"
# One term defined from the two inputs and two legal parameters: env_tax and vat_rate are cited.
FORMULA = {
    "terms": [
        {"code": "base", "op": "formula", "inputs": ["world_avg", "fx_rate", "env_tax", "vat_rate"]}
    ]
}
CONTRIBUTIONS = {"world": 195, "fx": 14, "tax": 0, "fund": -24, "proxy_error": 0}


def _cutoffs(n: int) -> list[datetime.date]:
    return [datetime.date(2024, 1, 1) + datetime.timedelta(days=30 * i) for i in range(n)]


@dataclass
class Fx:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    model: uuid.UUID
    published: uuid.UUID
    draft: uuid.UUID
    other_model: uuid.UUID
    other_version: uuid.UUID
    metric_version: uuid.UUID
    draft_metric_version: uuid.UUID
    formula_model: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM forecast.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            # The session registers psycopg2's UUID adapter; compare ids as the API writes them.
            value = row[0] if row else None
            return str(value) if isinstance(value, uuid.UUID) else value

    def run(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])

    # ------------------------------------------------------------ what ml-forecast would write

    def version(
        self,
        model: uuid.UUID,
        number: int,
        *,
        algorithm: str = "sarimax",
        mase: str = "0.82",
        in_sample: str = "0.70",
        frequency: str = "month",
        backtest: bool = True,
        created_by: str = "builder",
    ) -> uuid.UUID:
        """A draft version and, unless asked otherwise, its finished backtest."""
        version = self.seed.row(
            "forecast.model_version",
            org_id=self.org,
            model_id=model,
            version=number,
            algorithm=algorithm,
            features=Json([{"name": FEATURE, "future_assumption": "declared"}]),
            training_from=datetime.date(2020, 1, 1),
            training_to=datetime.date(2026, 9, 1),
            n_points=72,
            n_exog=1,
            model_card=Json({"target": "so_du_huy_dong", "limits": "chuỗi tháng"}),
            created_by=self.users[created_by],
        )
        if backtest:
            self.seed.row(
                "forecast.backtest_run",
                org_id=self.org,
                model_version_id=version,
                frequency=frequency,
                cutoffs=_cutoffs(20 if frequency in ("day", "week") else 6),
                horizon=3,
                metrics=Json({"h1": {"mase": mase}, "reconstruction_mae": 9.5}),
                baseline_metrics=Json({"h1": {"mase": 1}}),
                mase=decimal.Decimal(mase),
                in_sample_mase=decimal.Decimal(in_sample),
                started_at=NOW - datetime.timedelta(hours=2),
                finished_at=NOW - datetime.timedelta(hours=1),
            )
        return version

    def publish(self, model: uuid.UUID, version: uuid.UUID) -> None:
        backtest = self.one(
            "SELECT id FROM forecast.backtest_run WHERE model_version_id = %s", version
        )
        self.run(
            "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id = %s", version
        )
        self.run(
            "UPDATE forecast.model_version SET status = 'published', approved_by = %s,"
            " approved_at = now(), backtest_id = %s WHERE id = %s",
            self.users["owner"],
            backtest,
            version,
        )
        self.run(
            "UPDATE forecast.forecast_model SET current_version_id = %s WHERE id = %s",
            version,
            model,
        )

    def job(
        self,
        *,
        status: str = "queued",
        version: uuid.UUID | None = None,
        who: str = "analyst",
        horizon: int = 3,
        metric_code: str = "so_du_huy_dong",
        frequency: str = "month",
        scope: dict[str, Any] | None = None,
        space: uuid.UUID | None = None,
        cutoff_at: datetime.datetime | None = None,
        progress: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        return self.seed.row(
            "forecast.forecast_job",
            org_id=self.org,
            data_space_id=space or self.space,
            requested_by=self.users[who],
            metric_code=metric_code,
            scope=Json(SCOPE if scope is None else scope),
            frequency=frequency,
            horizon=horizon,
            cutoff_at=cutoff_at or NOW - datetime.timedelta(days=1),
            status=status,
            eta_at=NOW + datetime.timedelta(minutes=15),
            progress=Json(progress or {"step": status, "purpose": "forecast"}),
            model_version_id=version,
            finished_at=NOW if status in ("ready", "failed", "cancelled") else None,
        )

    def prediction(
        self,
        version: uuid.UUID,
        period: datetime.date,
        *,
        job: uuid.UUID | None = None,
        cutoff_at: datetime.datetime | None = None,
        point: int = 100,
        experimental: bool = False,
        scenario: uuid.UUID | None = None,
        formula: bool = False,
    ) -> uuid.UUID:
        return self.seed.row(
            "forecast.prediction",
            org_id=self.org,
            model_version_id=version,
            job_id=job,
            scenario_id=scenario,
            cutoff_at=cutoff_at or NOW - datetime.timedelta(days=1),
            period=period,
            point=point,
            lo80=point - 10,
            hi80=point + 10,
            lo95=point - 20,
            hi95=point + 20,
            interval_method="monte_carlo" if formula else "conformal",
            exog_uncertainty="scenario_fixed" if scenario else "simulated",
            contributions=Json(CONTRIBUTIONS if formula else {FEATURE: 1.5}),
            is_experimental=experimental,
        )

    def ledger(
        self,
        prediction: uuid.UUID,
        period: datetime.date,
        *,
        model: uuid.UUID | None = None,
        version: uuid.UUID | None = None,
        product: str = "e5ron92",
        status: str = "open",
        actual: int | None = None,
        miss: bool = False,
        created_at: datetime.datetime | None = None,
        job: uuid.UUID | None = None,
    ) -> uuid.UUID:
        values: dict[str, Any] = {
            "org_id": self.org,
            "forecast_model_id": model or self.model,
            "model_version_id": version or self.published,
            "prediction_id": prediction,
            "job_id": job,
            "period": period,
            "scope": Json({"product_code": product}),
            "issued_at": datetime.datetime.combine(period, datetime.time(), datetime.UTC)
            - datetime.timedelta(hours=4),
            "issued_via": "bulletin",
            "baseline_value": 19730,
            "point": 19850,
            "quantiles": Json(
                {"p02_5": 19400, "p10": 19560, "p50": 19850, "p90": 20120, "p97_5": 20300}
            ),
            "prob_up": decimal.Decimal("0.72"),
            "fund_probs": Json({"0": 0.8, "-500": 0.2}),
            "contributions": Json(CONTRIBUTIONS),
            "status": status,
            "created_at": created_at or NOW,
        }
        if status == "scored":
            values.update(
                actual=actual,
                abs_error=abs(19850 - (actual or 0)),
                crps=decimal.Decimal("80"),
                in80=not miss,
                in95=not miss,
                miss=miss,
                scored_at=NOW,
            )
        if status == "void":
            values["void_reason"] = "Kỳ bị gộp"
        return self.seed.row("forecast.ledger_entry", **values)

    def formula_version(self, number: int) -> tuple[uuid.UUID, uuid.UUID]:
        """A draft formula_mc version of the formula model's forecast_model and its formula_model."""
        version = self.version(self.formula_model, number, algorithm="formula_mc", frequency="week")
        formula = self.seed.row(
            "forecast.formula_model",
            org_id=self.org,
            model_version_id=version,
            metric_version_id=self.metric_version,
            product_code="e5ron92",
            formula=Json(FORMULA),
            window_rule=Json({"days": 7}),
            input_series=Json({"world": "public"}),
            owner_id=self.users["owner"],
            created_by=self.users["builder"],
        )
        return version, formula


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, *spaces: uuid.UUID) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


@pytest.fixture
def fx(seed: Seed) -> Fx:
    org = seed.org()
    steward = seed.row(
        "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
    )
    ask = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    upload = seed.row("org.role", org_id=org, code="UPLOAD", name="Upload", capabilities=["upload"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-FC", name="FC", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-FC-B", name="FC B", steward_id=keeper)
    users = {
        "owner": _user(seed, org, "owner", steward, space, other),
        "builder": _user(seed, org, "builder", steward, space),
        "steward": _user(seed, org, "steward", steward, space),
        "analyst": _user(seed, org, "analyst", ask, space),
        "analyst2": _user(seed, org, "analyst2", ask, space),
        "uploader": _user(seed, org, "uploader", upload, space),
    }
    model = seed.row(
        "forecast.forecast_model",
        org_id=org,
        data_space_id=space,
        metric_code="so_du_huy_dong",
        scope=Json(SCOPE),
        frequency="month",
        owner_id=users["owner"],
    )
    other_model = seed.row(
        "forecast.forecast_model",
        org_id=org,
        data_space_id=other,
        metric_code="so_du_huy_dong",
        scope=Json(SCOPE),
        frequency="month",
        owner_id=users["owner"],
    )
    metric = seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code="gia_co_so_xang",
        owner_id=users["owner"],
        current_version=1,
        status="active",
    )
    metric_version = seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=metric,
        version=1,
        display_name="Giá cơ sở xăng",
        expression=EXPRESSION,
        period_semantics="end_of_period",
        status="certified",
        certified_by=users["owner"],
        certified_at="2026-01-01T00:00:00Z",
        valid_from="2026-01-01T00:00:00Z",
    )
    draft_metric_version = seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=metric,
        version=2,
        display_name="Giá cơ sở xăng (nháp)",
        expression=EXPRESSION,
        period_semantics="end_of_period",
        status="draft",
    )
    formula_model = seed.row(
        "forecast.forecast_model",
        org_id=org,
        data_space_id=space,
        metric_code="gia_co_so_xang",
        scope=Json({"san_pham": "xang"}),
        frequency="week",
        owner_id=users["owner"],
    )
    out = Fx(
        org=org,
        space=space,
        other=other,
        users=users,
        model=model,
        published=uuid.uuid4(),
        draft=uuid.uuid4(),
        other_model=other_model,
        other_version=uuid.uuid4(),
        metric_version=metric_version,
        draft_metric_version=draft_metric_version,
        formula_model=formula_model,
        seed=seed,
    )
    out.published = out.version(model, 1)
    out.publish(model, out.published)
    out.draft = out.version(model, 2, mase="0.75")
    out.other_version = out.version(other_model, 1)
    return out


class Tokens:
    def __init__(self, fx: Fx) -> None:
        self.fx = fx

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.fx.org)}


def client(fx: Fx) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(fx))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _ask(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "metric_code": "so_du_huy_dong",
        "frequency": "month",
        "horizon": 3,
        "scope": SCOPE,
    }
    body.update(extra)
    return body


def _ahead(months: int) -> datetime.date:
    return (TODAY.replace(day=1) + datetime.timedelta(days=32 * months)).replace(day=1)


# ================================================================ forecast jobs (UC-15-01)


# AC-15-01-02
@pytest.mark.req("AC-15-01-02")
async def test_a_question_without_a_current_forecast_queues_a_job(fx: Fx) -> None:
    async with client(fx) as api:
        before = datetime.datetime.now(datetime.UTC)
        made = await api.post(
            "/v1/forecasts",
            json=_ask(notify_channels=["web"], assumptions=[]),
            headers=fx.headers("analyst"),
        )
        assert made.status_code == 202, made.text
        body = made.json()
        assert made.headers["Location"] == f"/v1/forecasts/{body['id']}"
        assert body["status"] == "queued" and body["poll_after_ms"] > 0 and body["eta_at"]
        job = uuid.UUID(body["id"])
        # BR-157: the job is cut at the request, so ml-forecast reads only data known by then.
        cutoff = fx.one("SELECT cutoff_at FROM forecast.forecast_job WHERE id = %s", job)
        assert (
            before - datetime.timedelta(seconds=5) <= cutoff <= datetime.datetime.now(datetime.UTC)
        )
        assert fx.one("SELECT requested_by FROM forecast.forecast_job WHERE id = %s", job) == str(
            fx.users["analyst"]
        )
        assert fx.one("SELECT data_space_id FROM forecast.forecast_job WHERE id = %s", job) == str(
            fx.space
        )
        events = fx.events(job)
        assert [e for e, _ in events] == ["forecast.job.state_changed"]
        payload = events[0][1]
        assert payload["job_id"] == str(job) and payload["status"] == "queued"
        assert payload["previous_status"] is None and payload["purpose"] == "forecast"

        fetched = await api.get(f"/v1/forecasts/{job}", headers=fx.headers("analyst"))
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "queued"
        # BR-159: no number before the backtest.
        assert fetched.json()["predictions"] == [] and fetched.json()["labels"] == []

        bad = await api.post("/v1/forecasts", json=_ask(horizon=0), headers=fx.headers("analyst"))
        assert bad.status_code == 422
        denied = await api.post("/v1/forecasts", json=_ask(), headers=fx.headers("uploader"))
        assert denied.status_code == 403
        no_space = await api.post(
            "/v1/forecasts", json=_ask(), headers={"Authorization": "Bearer kc-analyst"}
        )
        assert no_space.status_code == 400


# AC-15-01-01
@pytest.mark.req("AC-15-01-01")
async def test_a_current_forecast_of_the_released_model_is_answered_at_once(fx: Fx) -> None:
    job = fx.job(status="ready", version=fx.published, horizon=6)
    for i in range(1, 7):
        fx.prediction(fx.published, _ahead(i), job=job, point=100 + i)
    jobs_before = fx.one("SELECT count(*) FROM forecast.forecast_job WHERE org_id = %s", fx.org)
    async with client(fx) as api:
        answered = await api.post("/v1/forecasts", json=_ask(), headers=fx.headers("analyst"))
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["id"] == str(job) and body["status"] == "ready"
        assert len(body["predictions"]) == 3
        first = body["predictions"][0]
        assert {"point", "lo80", "hi80", "lo95", "hi95"} <= set(first)
        assert body["labels"] == ["approved"] and body["data_as_of"]
        assert (
            fx.one("SELECT count(*) FROM forecast.forecast_job WHERE org_id = %s", fx.org)
            == jobs_before
        )

        # Another scope has no released model: a new job.
        other = await api.post(
            "/v1/forecasts",
            json=_ask(scope={"khoi": "doanh_nghiep"}),
            headers=fx.headers("analyst"),
        )
        assert other.status_code == 202, other.text


async def test_a_forecast_whose_period_has_started_is_not_current(fx: Fx) -> None:
    # BR-159: once the first forecast period has started, a new job is due.
    job = fx.job(status="ready", version=fx.published)
    fx.prediction(fx.published, TODAY.replace(day=1), job=job)
    async with client(fx) as api:
        made = await api.post("/v1/forecasts", json=_ask(), headers=fx.headers("analyst"))
        assert made.status_code == 202, made.text
        assert made.json()["id"] != str(job)


async def test_a_ready_job_shows_its_predictions_and_labels(fx: Fx) -> None:
    job = fx.job(status="ready", version=fx.draft)
    fx.prediction(fx.draft, _ahead(1), job=job, experimental=True)
    fx.prediction(fx.draft, _ahead(2), job=job, experimental=True)
    async with client(fx) as api:
        got = await api.get(f"/v1/forecasts/{job}", headers=fx.headers("analyst"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert [p["period"] for p in body["predictions"]] == [
            _ahead(1).isoformat(),
            _ahead(2).isoformat(),
        ]
        # BR-158: output of an unreleased version is labelled experimental.
        assert body["labels"] == ["experimental"]
        assert body["data_as_of"] is not None

        denied = await api.get(f"/v1/forecasts/{job}", headers=fx.headers("uploader"))
        assert denied.status_code == 403
        elsewhere = fx.job(space=fx.other, who="owner")
        hidden = await api.get(f"/v1/forecasts/{elsewhere}", headers=fx.headers("owner"))
        assert hidden.status_code == 404
        shown = await api.get(f"/v1/forecasts/{elsewhere}", headers=fx.headers("owner", fx.other))
        assert shown.status_code == 200


async def test_the_event_stream_replays_the_job_and_resumes_after_last_event_id(fx: Fx) -> None:
    job = fx.job(status="ready", version=fx.published)
    fx.prediction(fx.published, _ahead(1), job=job)
    async with client(fx) as api:
        stream = await api.get(f"/v1/forecasts/{job}/events", headers=fx.headers("analyst"))
        assert stream.status_code == 200, stream.text
        assert stream.headers["content-type"].startswith("text/event-stream")
        for name in ("progress", "eta_changed", "result", "done"):
            assert f"event: {name}" in stream.text
        assert "id: 4" in stream.text
        resumed = await api.get(
            f"/v1/forecasts/{job}/events", headers={**fx.headers("analyst"), "Last-Event-ID": "2"}
        )
        assert "event: progress" not in resumed.text and "event: eta_changed" not in resumed.text
        assert "event: result" in resumed.text and "event: done" in resumed.text
        bad = await api.get(
            f"/v1/forecasts/{job}/events", headers={**fx.headers("analyst"), "Last-Event-ID": "x"}
        )
        assert bad.status_code == 400

        waiting = fx.job(status="waiting_human")
        live = await api.get(f"/v1/forecasts/{waiting}/events", headers=fx.headers("analyst"))
        assert "event: waiting_human" in live.text and "event: done" not in live.text

        denied = await api.get(f"/v1/forecasts/{job}/events", headers=fx.headers("uploader"))
        assert denied.status_code == 403
        elsewhere = fx.job(space=fx.other, who="owner")
        hidden = await api.get(f"/v1/forecasts/{elsewhere}/events", headers=fx.headers("owner"))
        assert hidden.status_code == 404


# AC-15-01-05
@pytest.mark.req("AC-15-01-05")
async def test_whoever_asked_cancels_a_running_job(fx: Fx) -> None:
    job = fx.job(status="backtesting")
    async with client(fx) as api:
        stranger = await api.post(f"/v1/forecasts/{job}:cancel", headers=fx.headers("analyst2"))
        assert stranger.status_code == 403, stranger.text
        denied = await api.post(f"/v1/forecasts/{job}:cancel", headers=fx.headers("uploader"))
        assert denied.status_code == 403

        cancelled = await api.post(
            f"/v1/forecasts/{job}:cancel",
            json={"reason": "Không cần nữa"},
            headers=fx.headers("analyst"),
        )
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["status"] == "cancelled" and body["finished_at"]
        assert body["progress"]["cancel_reason"] == "Không cần nữa"
        assert fx.one("SELECT status FROM forecast.forecast_job WHERE id = %s", job) == "cancelled"
        events = fx.events(job)
        assert events[-1][0] == "forecast.job.state_changed"
        assert events[-1][1]["previous_status"] == "backtesting"
        assert events[-1][1]["status"] == "cancelled"

        again = await api.post(f"/v1/forecasts/{job}:cancel", headers=fx.headers("analyst"))
        assert again.status_code == 409

        # A steward may stop someone else's job.
        other = fx.job(status="training")
        by_steward = await api.post(f"/v1/forecasts/{other}:cancel", headers=fx.headers("steward"))
        assert by_steward.status_code == 200, by_steward.text

        elsewhere = fx.job(space=fx.other, who="owner")
        hidden = await api.post(f"/v1/forecasts/{elsewhere}:cancel", headers=fx.headers("owner"))
        assert hidden.status_code == 404
        assert (
            fx.one("SELECT status FROM forecast.forecast_job WHERE id = %s", elsewhere) == "queued"
        )


# ================================================================ models and versions (UC-15-03 to 15-06)


async def test_models_list_with_their_mase_and_degradation(fx: Fx) -> None:
    fx.seed.row(
        "forecast.accuracy_log",
        org_id=fx.org,
        model_version_id=fx.published,
        period=datetime.date(2026, 8, 1),
        actual=100,
        point=98,
        abs_scaled_error=decimal.Decimal("0.4"),
        in80=True,
        in95=True,
        rolling_mase=decimal.Decimal("0.91"),
    )
    async with client(fx) as api:
        listed = await api.get("/v1/forecast-models", headers=fx.headers("analyst"))
        assert listed.status_code == 200, listed.text
        items = {i["id"]: i for i in listed.json()["items"]}
        assert set(items) == {str(fx.model), str(fx.formula_model)}
        mine = items[str(fx.model)]
        assert mine["current_version_id"] == str(fx.published)
        assert decimal.Decimal(mine["backtest_mase"]) == decimal.Decimal("0.82")
        assert decimal.Decimal(mine["rolling_mase"]) == decimal.Decimal("0.91")
        assert mine["degraded"] is False

        paged = await api.get("/v1/forecast-models?page_size=1", headers=fx.headers("analyst"))
        assert len(paged.json()["items"]) == 1 and paged.json()["next_page_token"]
        rest = await api.get(
            f"/v1/forecast-models?page_size=1&page_token={paged.json()['next_page_token']}",
            headers=fx.headers("analyst"),
        )
        assert {paged.json()["items"][0]["id"], rest.json()["items"][0]["id"]} == set(items)

        filtered = await api.get(
            "/v1/forecast-models?frequency=week", headers=fx.headers("analyst")
        )
        assert [i["id"] for i in filtered.json()["items"]] == [str(fx.formula_model)]
        none = await api.get("/v1/forecast-models?status=degraded", headers=fx.headers("analyst"))
        assert none.json()["items"] == []

        denied = await api.get("/v1/forecast-models", headers=fx.headers("uploader"))
        assert denied.status_code == 403

        one = await api.get(f"/v1/forecast-models/{fx.model}", headers=fx.headers("analyst"))
        assert one.status_code == 200, one.text
        assert one.json()["current_version"]["id"] == str(fx.published)
        assert one.json()["current_version"]["status"] == "published"
        hidden = await api.get(f"/v1/forecast-models/{fx.other_model}", headers=fx.headers("owner"))
        assert hidden.status_code == 404
        denied_one = await api.get(
            f"/v1/forecast-models/{fx.model}", headers=fx.headers("uploader")
        )
        assert denied_one.status_code == 403


async def test_a_model_of_another_organisation_does_not_exist(fx: Fx, seed: Seed) -> None:
    org = seed.org()
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-x-{org.hex[:6]}",
        username="x",
        display_name="X",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-X", name="X", steward_id=keeper)
    foreign = seed.row(
        "forecast.forecast_model",
        org_id=org,
        data_space_id=space,
        metric_code="so_du_huy_dong",
        frequency="month",
        owner_id=fx.users["owner"],
    )
    async with client(fx) as api:
        hidden = await api.get(f"/v1/forecast-models/{foreign}", headers=fx.headers("analyst"))
        assert hidden.status_code == 404
        retrain = await api.post(
            f"/v1/forecast-models/{foreign}:retrain", headers=fx.headers("owner")
        )
        assert retrain.status_code == 404


async def test_a_version_shows_its_card_and_backtest(fx: Fx) -> None:
    async with client(fx) as api:
        got = await api.get(
            f"/v1/forecast-model-versions/{fx.draft}", headers=fx.headers("analyst")
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["status"] == "draft" and body["model_card"]["target"] == "so_du_huy_dong"
        assert body["features"][0]["name"] == FEATURE

        backtest = await api.get(
            f"/v1/forecast-model-versions/{fx.draft}/backtest", headers=fx.headers("analyst")
        )
        assert backtest.status_code == 200, backtest.text
        assert decimal.Decimal(backtest.json()["mase"]) == decimal.Decimal("0.75")
        assert backtest.json()["baseline_metrics"] == {"h1": {"mase": 1}}
        assert len(backtest.json()["cutoffs"]) == 6

        bare = fx.version(fx.model, 3, backtest=False)
        none = await api.get(
            f"/v1/forecast-model-versions/{bare}/backtest", headers=fx.headers("analyst")
        )
        assert none.status_code == 404

        for path in (
            f"/v1/forecast-model-versions/{fx.other_version}",
            f"/v1/forecast-model-versions/{fx.other_version}/backtest",
        ):
            hidden = await api.get(path, headers=fx.headers("owner"))
            assert hidden.status_code == 404, path
        for path in (
            f"/v1/forecast-model-versions/{fx.draft}",
            f"/v1/forecast-model-versions/{fx.draft}/backtest",
        ):
            denied = await api.get(path, headers=fx.headers("uploader"))
            assert denied.status_code == 403, path


async def test_submitting_a_version_opens_the_owner_request(fx: Fx) -> None:
    async with client(fx) as api:
        denied = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:submit", headers=fx.headers("analyst")
        )
        assert denied.status_code == 403
        submitted = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:submit",
            json={"note": "MASE 0,75"},
            headers=fx.headers("builder"),
        )
        assert submitted.status_code == 200, submitted.text
        body = submitted.json()
        assert body["status"] == "pending_approval" and body["backtest_id"]
        assert body["model_card"]["review"][0]["action"] == "submit"
        assert body["model_card"]["review"][0]["note"] == "MASE 0,75"
        events = dict(fx.events(fx.draft))
        sent = events["forecast.model_version.submitted"]
        assert sent["hitl_kind"] == "forecast_model"
        assert sent["assignee_id"] == str(fx.users["owner"])
        assert sent["model_id"] == str(fx.model) and sent["backtest_id"] == body["backtest_id"]

        again = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:submit", headers=fx.headers("builder")
        )
        assert again.status_code == 409
        hidden = await api.post(
            f"/v1/forecast-model-versions/{fx.other_version}:submit", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# AC-15-04-06
@pytest.mark.req("AC-15-04-06")
async def test_a_version_not_beating_seasonal_naive_is_not_submitted(fx: Fx) -> None:
    even = fx.version(fx.model, 3, mase="1.00")
    bare = fx.version(fx.model, 4, backtest=False)
    async with client(fx) as api:
        for version in (even, bare):
            refused = await api.post(
                f"/v1/forecast-model-versions/{version}:submit", headers=fx.headers("builder")
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["code"] == "business_rule_violated"
            assert (
                fx.one("SELECT status FROM forecast.model_version WHERE id = %s", version)
                == "draft"
            )
        assert fx.events(even) == []


# AC-15-03-04
@pytest.mark.req("AC-15-03-04")
async def test_the_seasonal_naive_baseline_is_never_submitted(fx: Fx) -> None:
    baseline = fx.version(fx.model, 3, algorithm="seasonal_naive", mase="0.5")
    async with client(fx) as api:
        refused = await api.post(
            f"/v1/forecast-model-versions/{baseline}:submit", headers=fx.headers("builder")
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"


async def _submit(api: httpx.AsyncClient, fx: Fx, version: uuid.UUID) -> None:
    sent = await api.post(
        f"/v1/forecast-model-versions/{version}:submit", headers=fx.headers("builder")
    )
    assert sent.status_code == 200, sent.text


# AC-15-04-01
@pytest.mark.req("AC-15-04-01")
async def test_the_owner_publishes_a_version_and_the_old_one_retires(fx: Fx) -> None:
    async with client(fx) as api:
        await _submit(api, fx, fx.draft)
        approved = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:approve",
            json={"note": "Tốt hơn bản cũ"},
            headers=fx.headers("owner"),
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "published"
        assert body["approved_by"] == str(fx.users["owner"]) and body["approved_at"]
        assert body["model_card"]["review"][-1]["action"] == "approve"
        assert (
            fx.one("SELECT status FROM forecast.model_version WHERE id = %s", fx.published)
            == "retired"
        )
        assert fx.one(
            "SELECT current_version_id FROM forecast.forecast_model WHERE id = %s", fx.model
        ) == str(fx.draft)
        published = dict(fx.events(fx.draft))["forecast.model.published"]
        assert published["previous_version_id"] == str(fx.published)
        assert published["model_id"] == str(fx.model)
        assert published["approved_by"] == str(fx.users["owner"])
        assert [e for e, _ in fx.events(fx.published)] == ["forecast.model_version.retired"]

        again = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:approve", headers=fx.headers("owner")
        )
        assert again.status_code == 409


# AC-15-04-04
@pytest.mark.req("AC-15-04-04")
async def test_only_the_owner_who_did_not_build_the_version_decides(fx: Fx) -> None:
    by_owner = fx.version(fx.model, 3, created_by="owner")
    async with client(fx) as api:
        await _submit(api, fx, fx.draft)
        sent = await api.post(
            f"/v1/forecast-model-versions/{by_owner}:submit", headers=fx.headers("owner")
        )
        assert sent.status_code == 200, sent.text
        for action, body in ((":approve", None), (":reject", {"reason": "Không đạt"})):
            # Whoever built it, another steward, or an analyst: none of them decides (BR-158).
            for who, version, status in (
                ("builder", fx.draft, 403),
                ("steward", fx.draft, 403),
                ("owner", by_owner, 403),
                ("analyst", fx.draft, 403),
            ):
                refused = await api.post(
                    f"/v1/forecast-model-versions/{version}{action}",
                    json=body,
                    headers=fx.headers(who),
                )
                assert refused.status_code == status, (action, who, refused.text)
                if who != "analyst":
                    assert refused.json()["code"] == "policy_denied"
        assert fx.one("SELECT status FROM forecast.model_version WHERE id = %s", fx.draft) == (
            "pending_approval"
        )
        hidden = await api.post(
            f"/v1/forecast-model-versions/{fx.other_version}:approve", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# AC-15-03-07
@pytest.mark.req("AC-15-03-07")
async def test_an_overfitted_version_is_refused_by_the_database(fx: Fx) -> None:
    # MASE 0.9 beats seasonal naive, but is three times the in-sample 0.3 (BR-172).
    overfit = fx.version(fx.model, 3, mase="0.90", in_sample="0.30")
    async with client(fx) as api:
        await _submit(api, fx, overfit)
        refused = await api.post(
            f"/v1/forecast-model-versions/{overfit}:approve", headers=fx.headers("owner")
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        assert fx.one("SELECT status FROM forecast.model_version WHERE id = %s", fx.published) == (
            "published"
        )


# AC-15-04-02
@pytest.mark.req("AC-15-04-02")
async def test_rejecting_keeps_the_released_version_and_the_reason(fx: Fx) -> None:
    async with client(fx) as api:
        draft_reject = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:reject",
            json={"reason": "Chưa gửi"},
            headers=fx.headers("owner"),
        )
        assert draft_reject.status_code == 409
        await _submit(api, fx, fx.draft)
        blank = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:reject",
            json={"reason": "   "},
            headers=fx.headers("owner"),
        )
        assert blank.status_code == 422
        rejected = await api.post(
            f"/v1/forecast-model-versions/{fx.draft}:reject",
            json={"reason": "Biến Tết thiếu năm 2021"},
            headers=fx.headers("owner"),
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        review = rejected.json()["model_card"]["review"][-1]
        assert review["action"] == "reject" and review["note"] == "Biến Tết thiếu năm 2021"
        assert review["by"] == str(fx.users["owner"])
        assert fx.one(
            "SELECT current_version_id FROM forecast.forecast_model WHERE id = %s", fx.model
        ) == str(fx.published)
        assert fx.one("SELECT status FROM forecast.model_version WHERE id = %s", fx.published) == (
            "published"
        )
        event = dict(fx.events(fx.draft))["forecast.model_version.rejected"]
        assert event["rejected_by"] == str(fx.users["owner"])


# AC-15-06-05
@pytest.mark.req("AC-15-06-05")
async def test_retraining_queues_one_job_per_model(fx: Fx) -> None:
    async with client(fx) as api:
        denied = await api.post(
            f"/v1/forecast-models/{fx.model}:retrain", headers=fx.headers("analyst")
        )
        assert denied.status_code == 403
        future = await api.post(
            f"/v1/forecast-models/{fx.model}:retrain",
            json={"training_to": (TODAY + datetime.timedelta(days=3)).isoformat()},
            headers=fx.headers("steward"),
        )
        assert future.status_code == 422, future.text

        started = await api.post(
            f"/v1/forecast-models/{fx.model}:retrain",
            json={"reason": "Có số tháng 9", "training_to": "2026-09-01"},
            headers=fx.headers("steward"),
        )
        assert started.status_code == 202, started.text
        job = uuid.UUID(started.json()["id"])
        assert started.headers["Location"] == f"/v1/forecasts/{job}"
        progress = fx.one("SELECT progress FROM forecast.forecast_job WHERE id = %s", job)
        assert progress["purpose"] == "retrain" and progress["model_id"] == str(fx.model)
        assert progress["training_to"] == "2026-09-01" and progress["reason"] == "Có số tháng 9"
        # The horizon is the released version's backtest horizon.
        assert fx.one("SELECT horizon FROM forecast.forecast_job WHERE id = %s", job) == 3
        event = dict(fx.events(job))["forecast.job.state_changed"]
        assert event["purpose"] == "retrain" and event["status"] == "queued"

        twice = await api.post(
            f"/v1/forecast-models/{fx.model}:retrain", headers=fx.headers("steward")
        )
        assert twice.status_code == 409

        hidden = await api.post(
            f"/v1/forecast-models/{fx.other_model}:retrain", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404

        fx.run(
            "UPDATE forecast.forecast_model SET status = 'retired' WHERE id = %s", fx.formula_model
        )
        retired = await api.post(
            f"/v1/forecast-models/{fx.formula_model}:retrain", headers=fx.headers("steward")
        )
        assert retired.status_code == 409


# AC-15-05-02
@pytest.mark.req("AC-15-05-02")
async def test_a_scenario_is_queued_on_the_trained_version_with_its_notice(fx: Fx) -> None:
    body = {
        "name": "Lãi suất +0,5",
        "assumptions": [
            {"feature": FEATURE, "from_period": "2026-12-01", "change_kind": "tăng", "value": "0.5"}
        ],
    }
    backtests = fx.one("SELECT count(*) FROM forecast.backtest_run WHERE org_id = %s", fx.org)
    async with client(fx) as api:
        made = await api.post(
            f"/v1/forecast-models/{fx.model}/scenarios", json=body, headers=fx.headers("analyst")
        )
        assert made.status_code == 202, made.text
        out = made.json()
        scenario = uuid.UUID(out["id"])
        assert made.headers["Location"] == (
            f"/v1/forecast-models/{fx.model}/predictions?scenario_id={scenario}"
        )
        assert out["model_version_id"] == str(fx.published) and out["status"] == "queued"
        # BR-173: the sentence carries the assumption.
        assert "Nếu lai_suat_dieu_hanh tăng 0.5 từ 2026-12-01" in out["notice"]
        assert "không phải ước lượng tác động nhân quả" in out["notice"]
        assert (
            fx.one("SELECT name FROM forecast.scenario WHERE id = %s", scenario) == "Lãi suất +0,5"
        )
        event = dict(fx.events(scenario))["forecast.scenario.created"]
        assert event["model_version_id"] == str(fx.published) and event["model_id"] == str(fx.model)
        # AC-15-05-02: the trained model reruns, no new backtest.
        assert (
            fx.one("SELECT count(*) FROM forecast.backtest_run WHERE org_id = %s", fx.org)
            == backtests
        )

        twice = await api.post(
            f"/v1/forecast-models/{fx.model}/scenarios", json=body, headers=fx.headers("analyst")
        )
        assert twice.status_code == 409
        denied = await api.post(
            f"/v1/forecast-models/{fx.model}/scenarios", json=body, headers=fx.headers("uploader")
        )
        assert denied.status_code == 403
        hidden = await api.post(
            f"/v1/forecast-models/{fx.other_model}/scenarios",
            json=body,
            headers=fx.headers("owner"),
        )
        assert hidden.status_code == 404
        # A model with no version yet has nothing to rerun.
        empty = await api.post(
            f"/v1/forecast-models/{fx.formula_model}/scenarios",
            json=body,
            headers=fx.headers("analyst"),
        )
        assert empty.status_code == 409


# AC-15-05-06
@pytest.mark.req("AC-15-05-06")
async def test_a_scenario_on_a_variable_the_model_does_not_use_does_not_apply(fx: Fx) -> None:
    body = {
        "name": "Tỷ giá +2%",
        "assumptions": [{"feature": "ty_gia", "from_period": "2026-12-01", "change_kind": "tăng"}],
    }
    async with client(fx) as api:
        refused = await api.post(
            f"/v1/forecast-models/{fx.model}/scenarios", json=body, headers=fx.headers("analyst")
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        assert fx.one("SELECT count(*) FROM forecast.scenario WHERE model_id = %s", fx.model) == 0


async def test_predictions_are_read_point_in_time(fx: Fx) -> None:
    early = NOW - datetime.timedelta(days=10)
    late = NOW - datetime.timedelta(days=2)
    period = _ahead(1)
    fx.prediction(fx.published, period, cutoff_at=early, point=100)
    fx.prediction(fx.published, period, cutoff_at=late, point=120)
    fx.prediction(fx.published, _ahead(2), cutoff_at=late, point=130)
    # Cut after now: not known yet.
    fx.prediction(fx.published, _ahead(3), cutoff_at=NOW + datetime.timedelta(days=1), point=140)
    async with client(fx) as api:
        latest = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions", headers=fx.headers("analyst")
        )
        assert latest.status_code == 200, latest.text
        body = latest.json()
        assert [(p["period"], decimal.Decimal(p["point"])) for p in body["items"]] == [
            (period.isoformat(), 120),
            (_ahead(2).isoformat(), 130),
        ]
        assert body["labels"] == ["approved"] and body["model_version_id"] == str(fx.published)
        # BR-157: the assumptions always come with the numbers.
        assert body["assumptions"][0]["name"] == FEATURE

        as_of = (early + datetime.timedelta(days=1)).isoformat()
        then = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions",
            params={"as_of": as_of},
            headers=fx.headers("analyst"),
        )
        assert [decimal.Decimal(p["point"]) for p in then.json()["items"]] == [100]

        ranged = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions",
            params={"from": _ahead(2).isoformat()},
            headers=fx.headers("analyst"),
        )
        assert [p["period"] for p in ranged.json()["items"]] == [_ahead(2).isoformat()]

        paged = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions?page_size=1", headers=fx.headers("analyst")
        )
        rest = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions?page_size=1"
            f"&page_token={paged.json()['next_page_token']}",
            headers=fx.headers("analyst"),
        )
        assert [p["period"] for p in rest.json()["items"]] == [_ahead(2).isoformat()]

        wrong = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions",
            params={"model_version_id": str(fx.other_version)},
            headers=fx.headers("analyst"),
        )
        assert wrong.status_code == 422
        denied = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions", headers=fx.headers("uploader")
        )
        assert denied.status_code == 403
        hidden = await api.get(
            f"/v1/forecast-models/{fx.other_model}/predictions", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


async def test_scenario_predictions_carry_the_scenario_notice(fx: Fx) -> None:
    assumptions = [{"feature": FEATURE, "from_period": "2026-12-01", "change_kind": "giữ nguyên"}]
    scenario = fx.seed.row(
        "forecast.scenario",
        org_id=fx.org,
        model_id=fx.model,
        name="Giữ nguyên",
        assumptions=Json(assumptions),
        created_by=fx.users["analyst"],
    )
    other = fx.seed.row(
        "forecast.scenario",
        org_id=fx.org,
        model_id=fx.other_model,
        name="B",
        assumptions=Json(assumptions),
        created_by=fx.users["owner"],
    )
    fx.prediction(fx.published, _ahead(1), point=100)
    fx.prediction(fx.published, _ahead(1), point=90, scenario=scenario)
    async with client(fx) as api:
        got = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions",
            params={"scenario_id": str(scenario)},
            headers=fx.headers("analyst"),
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert [decimal.Decimal(p["point"]) for p in body["items"]] == [90]
        assert body["items"][0]["exog_uncertainty"] == "scenario_fixed"
        assert body["assumptions"] == assumptions and "giữ nguyên" in body["notice"]
        foreign = await api.get(
            f"/v1/forecast-models/{fx.model}/predictions",
            params={"scenario_id": str(other)},
            headers=fx.headers("analyst"),
        )
        assert foreign.status_code == 404


# AC-15-06-01
@pytest.mark.req("AC-15-06-01")
async def test_accuracy_lists_errors_with_rolling_and_backtest_mase(fx: Fx) -> None:
    for month, inside, recorded in ((7, True, 20), (8, False, 10), (9, True, 1)):
        fx.seed.row(
            "forecast.accuracy_log",
            org_id=fx.org,
            model_version_id=fx.published,
            period=datetime.date(2026, month, 1),
            actual=100,
            point=95,
            abs_scaled_error=decimal.Decimal("0.5"),
            in80=inside,
            in95=True,
            rolling_mase=decimal.Decimal(f"0.{month}0"),
            recorded_at=NOW - datetime.timedelta(days=recorded),
        )
    async with client(fx) as api:
        got = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy", headers=fx.headers("analyst")
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert [i["period"] for i in body["items"]] == ["2026-09-01", "2026-08-01", "2026-07-01"]
        assert decimal.Decimal(body["rolling_mase"]) == decimal.Decimal("0.90")
        assert decimal.Decimal(body["backtest_mase"]) == decimal.Decimal("0.82")
        assert decimal.Decimal(body["in80_rate"]) == decimal.Decimal("0.6667")
        assert decimal.Decimal(body["in95_rate"]) == 1 and body["degraded"] is False

        then = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy",
            params={"as_of": (NOW - datetime.timedelta(days=5)).isoformat()},
            headers=fx.headers("analyst"),
        )
        assert [i["period"] for i in then.json()["items"]] == ["2026-08-01", "2026-07-01"]
        ranged = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy",
            params={"from": "2026-08-01", "to": "2026-08-31"},
            headers=fx.headers("analyst"),
        )
        assert [i["period"] for i in ranged.json()["items"]] == ["2026-08-01"]
        paged = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy?page_size=2", headers=fx.headers("analyst")
        )
        rest = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy?page_size=2"
            f"&page_token={paged.json()['next_page_token']}",
            headers=fx.headers("analyst"),
        )
        assert [i["period"] for i in rest.json()["items"]] == ["2026-07-01"]

        denied = await api.get(
            f"/v1/forecast-models/{fx.model}/accuracy", headers=fx.headers("uploader")
        )
        assert denied.status_code == 403
        hidden = await api.get(
            f"/v1/forecast-models/{fx.other_model}/accuracy", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# ================================================================ formula forecasts (UC-15-07)


def _formula(fx: Fx, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model_id": str(fx.formula_model),
        "metric_version_id": str(fx.metric_version),
        "product_code": "e5ron92",
        "formula": FORMULA,
        "window_rule": {"days": 7},
        "input_series": {"world": "public"},
    }
    body.update(extra)
    return body


async def _new_formula(api: httpx.AsyncClient, fx: Fx, **extra: Any) -> dict[str, Any]:
    made = await api.post(
        "/v1/formula-models", json=_formula(fx, **extra), headers=fx.headers("builder")
    )
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def test_a_formula_model_is_compiled_from_a_certified_metric_version(fx: Fx) -> None:
    async with client(fx) as api:
        denied = await api.post(
            "/v1/formula-models", json=_formula(fx), headers=fx.headers("analyst")
        )
        assert denied.status_code == 403
        made = await api.post(
            "/v1/formula-models", json=_formula(fx), headers=fx.headers("builder")
        )
        assert made.status_code == 201, made.text
        body = made.json()
        assert made.headers["Location"] == f"/v1/formula-models/{body['id']}"
        assert body["version_status"] == "draft" and body["model_id"] == str(fx.formula_model)
        assert body["owner_id"] == str(fx.users["owner"]) and body["n_draws"] == 10_000
        version = uuid.UUID(body["model_version_id"])
        assert (
            fx.one("SELECT algorithm FROM forecast.model_version WHERE id = %s", version)
            == "formula_mc"
        )
        assert fx.one("SELECT version FROM forecast.model_version WHERE id = %s", version) == 1
        assert [e for e, _ in fx.events(version)] == ["forecast.model_version.created"]
        created = dict(fx.events(body["id"]))["forecast.formula_model.created"]
        assert created["metric_version_id"] == str(fx.metric_version)
        assert created["model_version_id"] == str(version)

        second = await _new_formula(api, fx, product_code="ron95")
        assert (
            fx.one(
                "SELECT version FROM forecast.model_version WHERE id = %s",
                second["model_version_id"],
            )
            == 2
        )

        listed = await api.get("/v1/formula-models", headers=fx.headers("analyst"))
        assert listed.status_code == 200, listed.text
        assert {i["id"] for i in listed.json()["items"]} == {body["id"], second["id"]}
        only = await api.get(
            "/v1/formula-models",
            params={"product_code": "ron95", "status": "draft"},
            headers=fx.headers("analyst"),
        )
        assert [i["id"] for i in only.json()["items"]] == [second["id"]]
        none = await api.get(
            "/v1/formula-models", params={"status": "published"}, headers=fx.headers("analyst")
        )
        assert none.json()["items"] == []

        got = await api.get(f"/v1/formula-models/{body['id']}", headers=fx.headers("analyst"))
        assert got.status_code == 200, got.text
        assert got.json()["formula"] == FORMULA and got.json()["input_series"] == {
            "world": "public"
        }

        for path in ("/v1/formula-models", f"/v1/formula-models/{body['id']}"):
            refused = await api.get(path, headers=fx.headers("uploader"))
            assert refused.status_code == 403, path
        # Another Data Space neither lists nor shows it.
        elsewhere = await api.get("/v1/formula-models", headers=fx.headers("owner", fx.other))
        assert elsewhere.json()["items"] == []
        hidden = await api.get(
            f"/v1/formula-models/{body['id']}", headers=fx.headers("owner", fx.other)
        )
        assert hidden.status_code == 404


async def test_a_formula_model_cites_only_the_certified_expression(fx: Fx) -> None:
    odd = {"terms": [{"code": "base", "op": "formula", "inputs": ["world_avg", "import_tax"]}]}
    async with client(fx) as api:
        for extra, status, code in (
            ({"formula": odd}, 422, "business_rule_violated"),
            ({"metric_version_id": str(fx.draft_metric_version)}, 422, "business_rule_violated"),
            ({"model_id": str(fx.model)}, 422, "validation_failed"),
            ({"model_id": str(fx.other_model)}, 422, "validation_failed"),
            ({"metric_version_id": str(uuid.uuid4())}, 422, "validation_failed"),
            ({"n_draws": 9_999}, 422, "validation_failed"),
        ):
            refused = await api.post(
                "/v1/formula-models", json=_formula(fx, **extra), headers=fx.headers("builder")
            )
            assert refused.status_code == status, (extra, refused.text)
            assert refused.json()["code"] == code, extra
        assert (
            fx.one(
                "SELECT count(*) FROM forecast.model_version WHERE model_id = %s", fx.formula_model
            )
            == 0
        )


def _component(code: str, product: str = "e5ron92", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "product_code": product,
        "value": "4000" if code == "env_tax" else "10",
        "unit": "vnd_per_litre" if code == "env_tax" else "percent",
        "valid_from": "2026-01-01T00:00:00Z",
        "legal_doc_number": "30/2025/QH15",
        "legal_doc_url": "https://vbpl.vn/30-2025-qh15",
    }
    body.update(extra)
    return body


async def _propose(
    api: httpx.AsyncClient, fx: Fx, formula: str, body: dict[str, Any]
) -> dict[str, Any]:
    made = await api.post(
        f"/v1/formula-models/{formula}/components", json=body, headers=fx.headers("builder")
    )
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


# AC-15-07-04
@pytest.mark.req("AC-15-07-04")
async def test_a_parameter_is_proposed_then_certified_by_someone_else(fx: Fx) -> None:
    async with client(fx) as api:
        formula = (await _new_formula(api, fx))["id"]
        denied = await api.post(
            f"/v1/formula-models/{formula}/components",
            json=_component("env_tax"),
            headers=fx.headers("analyst"),
        )
        assert denied.status_code == 403
        for body in (
            _component("import_tax"),
            _component("env_tax", product="ron95"),
            _component("env_tax", valid_to="2025-12-01T00:00:00Z"),
            _component("env_tax", legal_doc_url="ftp://vbpl.vn/x"),
        ):
            refused = await api.post(
                f"/v1/formula-models/{formula}/components", json=body, headers=fx.headers("builder")
            )
            assert refused.status_code == 422, (body, refused.text)

        old = await _propose(api, fx, formula, _component("env_tax"))
        assert old["status"] == "pending_review" and old["entered_by"] == str(fx.users["builder"])
        submitted = dict(fx.events(old["id"]))["forecast.formula_component.submitted"]
        assert submitted["hitl_kind"] == "formula_component"
        assert submitted["assignee_id"] == str(fx.users["owner"])
        assert submitted["formula_model_id"] == formula

        own = await api.post(
            f"/v1/formula-components/{old['id']}:certify", headers=fx.headers("builder")
        )
        assert own.status_code == 403, own.text
        assert own.json()["code"] == "policy_denied"
        by_analyst = await api.post(
            f"/v1/formula-components/{old['id']}:certify", headers=fx.headers("analyst")
        )
        assert by_analyst.status_code == 403
        hidden = await api.post(
            f"/v1/formula-components/{old['id']}:certify", headers=fx.headers("owner", fx.other)
        )
        assert hidden.status_code == 404

        certified = await api.post(
            f"/v1/formula-components/{old['id']}:certify",
            json={"note": "Đối chiếu văn bản"},
            headers=fx.headers("steward"),
        )
        assert certified.status_code == 200, certified.text
        assert certified.json()["status"] == "certified"
        assert certified.json()["certified_by"] == str(fx.users["steward"])
        assert (
            dict(fx.events(old["id"]))["forecast.formula_component.certified"]["supersedes_id"]
            is None
        )
        again = await api.post(
            f"/v1/formula-components/{old['id']}:certify", headers=fx.headers("steward")
        )
        assert again.status_code == 409

        # A value starting before the one in force is refused (BR-182).
        earlier = await _propose(
            api, fx, formula, _component("env_tax", valid_from="2025-06-01T00:00:00Z", value="3000")
        )
        before = await api.post(
            f"/v1/formula-components/{earlier['id']}:certify", headers=fx.headers("steward")
        )
        assert before.status_code == 409, before.text

        # A new law: the new value closes the old one at its own valid_from.
        start = (NOW - datetime.timedelta(days=1)).replace(microsecond=0)
        new = await _propose(
            api, fx, formula, _component("env_tax", valid_from=start.isoformat(), value="2000")
        )
        done = await api.post(
            f"/v1/formula-components/{new['id']}:certify", headers=fx.headers("owner")
        )
        assert done.status_code == 200, done.text
        assert (
            dict(fx.events(new["id"]))["forecast.formula_component.certified"]["supersedes_id"]
            == old["id"]
        )
        assert fx.one("SELECT status FROM forecast.formula_component WHERE id = %s", old["id"]) == (
            "superseded"
        )
        assert (
            fx.one("SELECT valid_to FROM forecast.formula_component WHERE id = %s", old["id"])
            == start
        )

        # ?at answers the value in force then, certified by then.
        listed = await api.get(
            f"/v1/formula-models/{formula}/components", headers=fx.headers("analyst")
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 3
        # Yesterday nothing was certified yet: point in time, a value counts once certified.
        past = await api.get(
            f"/v1/formula-models/{formula}/components",
            params={"at": (start - datetime.timedelta(hours=1)).isoformat()},
            headers=fx.headers("analyst"),
        )
        assert past.status_code == 200, past.text
        assert past.json()["items"] == []
        current = await api.get(
            f"/v1/formula-models/{formula}/components",
            params={"at": datetime.datetime.now(datetime.UTC).isoformat()},
            headers=fx.headers("analyst"),
        )
        assert [i["id"] for i in current.json()["items"]] == [new["id"]]
        pending = await api.get(
            f"/v1/formula-models/{formula}/components",
            params={"status": "pending_review"},
            headers=fx.headers("analyst"),
        )
        assert [i["id"] for i in pending.json()["items"]] == [earlier["id"]]
        refused = await api.get(
            f"/v1/formula-models/{formula}/components", headers=fx.headers("uploader")
        )
        assert refused.status_code == 403
        elsewhere = await api.get(
            f"/v1/formula-models/{formula}/components", headers=fx.headers("owner", fx.other)
        )
        assert elsewhere.status_code == 404


# AC-15-07-06
@pytest.mark.req("AC-15-07-06")
async def test_a_run_without_every_certified_parameter_gives_no_number(fx: Fx) -> None:
    period = TODAY + datetime.timedelta(days=7)
    async with client(fx) as api:
        formula = (await _new_formula(api, fx))["id"]
        env = await _propose(api, fx, formula, _component("env_tax"))
        certified = await api.post(
            f"/v1/formula-components/{env['id']}:certify", headers=fx.headers("steward")
        )
        assert certified.status_code == 200, certified.text
        jobs = fx.one("SELECT count(*) FROM forecast.forecast_job WHERE org_id = %s", fx.org)
        missing = await api.post(
            f"/v1/formula-models/{formula}/runs",
            json={"period": period.isoformat()},
            headers=fx.headers("analyst"),
        )
        assert missing.status_code == 422, missing.text
        problem = missing.json()
        assert problem["code"] == "business_rule_violated" and "CHƯA CÓ" in problem["detail"]
        assert [e["field"] for e in problem["errors"]] == ["vat_rate"]
        # The message names the parameter and who certifies it.
        assert str(fx.users["owner"]) in problem["errors"][0]["message"]
        assert (
            fx.one("SELECT count(*) FROM forecast.forecast_job WHERE org_id = %s", fx.org) == jobs
        )


async def test_a_run_of_a_certified_formula_is_queued_and_unreleased_output_is_not_issued(
    fx: Fx,
) -> None:
    period = TODAY + datetime.timedelta(days=7)
    async with client(fx) as api:
        formula = await _new_formula(api, fx)
        sibling = await _new_formula(api, fx, product_code="ron95")
        for code, product in (("env_tax", "e5ron92"), ("env_tax", "ron95"), ("vat_rate", "*")):
            target = formula if product != "ron95" else sibling
            proposed = await _propose(api, fx, target["id"], _component(code, product=product))
            ok = await api.post(
                f"/v1/formula-components/{proposed['id']}:certify", headers=fx.headers("steward")
            )
            assert ok.status_code == 200, ok.text

        # AC-15-07-05: an unreleased version is labelled, never issued.
        issued = await api.post(
            f"/v1/formula-models/{formula['id']}/runs",
            json={"period": period.isoformat(), "issue": "bulletin"},
            headers=fx.headers("analyst"),
        )
        assert issued.status_code == 422, issued.text
        unknown = await api.post(
            f"/v1/formula-models/{formula['id']}/runs",
            json={"period": period.isoformat(), "product_codes": ["mazut"]},
            headers=fx.headers("analyst"),
        )
        assert unknown.status_code == 422
        denied = await api.post(
            f"/v1/formula-models/{formula['id']}/runs",
            json={"period": period.isoformat()},
            headers=fx.headers("uploader"),
        )
        assert denied.status_code == 403
        hidden = await api.post(
            f"/v1/formula-models/{formula['id']}/runs",
            json={"period": period.isoformat()},
            headers=fx.headers("owner", fx.other),
        )
        assert hidden.status_code == 404

        run = await api.post(
            f"/v1/formula-models/{formula['id']}/runs",
            json={"period": period.isoformat(), "product_codes": ["e5ron92", "ron95"]},
            headers=fx.headers("analyst"),
        )
        assert run.status_code == 202, run.text
        job = uuid.UUID(run.json()["id"])
        assert run.headers["Location"] == f"/v1/forecasts/{job}"
        progress = fx.one("SELECT progress FROM forecast.forecast_job WHERE id = %s", job)
        assert progress["purpose"] == "formula_run" and progress["period"] == period.isoformat()
        assert progress["formula_model_ids"] == [formula["id"], sibling["id"]]
        assert (
            fx.one("SELECT model_version_id FROM forecast.forecast_job WHERE id = %s", job)
            == (formula["model_version_id"])
        )
        event = dict(fx.events(job))["forecast.job.state_changed"]
        assert event["purpose"] == "formula_run" and event["status"] == "queued"


async def test_the_distribution_of_a_ready_formula_job_carries_the_fund_sentence(fx: Fx) -> None:
    version, _ = fx.formula_version(1)
    job = fx.job(
        status="ready",
        version=version,
        metric_code="gia_co_so_xang",
        frequency="week",
        horizon=1,
        scope={"product_codes": ["e5ron92"]},
    )
    period = TODAY + datetime.timedelta(days=7)
    issued = fx.prediction(version, period, job=job, point=19850, experimental=True, formula=True)
    entry = fx.ledger(issued, period, model=fx.formula_model, version=version, job=job)
    async with client(fx) as api:
        got = await api.get(f"/v1/forecasts/{job}/distribution", headers=fx.headers("analyst"))
        assert got.status_code == 200, got.text
        body = got.json()
        # BR-184: the Fund part always carries this sentence.
        assert body["fund_note"].startswith("Quyết định Quỹ do liên Bộ quyết")
        item = body["items"][0]
        assert item["product_code"] == "e5ron92" and item["ledger_entry_id"] == str(entry)
        assert item["quantiles"]["p10"] == 19560 and decimal.Decimal(
            item["prob_up"]
        ) == decimal.Decimal("0.72")
        assert set(item["contributions"]) == set(CONTRIBUTIONS) and item["is_experimental"] is True
        none = await api.get(
            f"/v1/forecasts/{job}/distribution",
            params={"product_code": "ron95"},
            headers=fx.headers("analyst"),
        )
        assert none.json()["items"] == []

        queued = fx.job()
        early = await api.get(f"/v1/forecasts/{queued}/distribution", headers=fx.headers("analyst"))
        assert early.status_code == 409
        denied = await api.get(f"/v1/forecasts/{job}/distribution", headers=fx.headers("uploader"))
        assert denied.status_code == 403
        elsewhere = fx.job(space=fx.other, who="owner")
        hidden = await api.get(
            f"/v1/forecasts/{elsewhere}/distribution", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# ================================================================ forecast ledger (UC-15-08)


@dataclass
class Ledger:
    scored: uuid.UUID
    missed: uuid.UUID
    open: uuid.UUID
    void: uuid.UUID
    foreign: uuid.UUID


def _ledger(fx: Fx) -> Ledger:
    # Created in an order unlike the periods', so a cursor on issue time alone would skip lines.
    p = {
        d: fx.prediction(fx.published, d)
        for d in (
            datetime.date(2026, 6, 25),
            datetime.date(2026, 7, 2),
            datetime.date(2026, 7, 9),
            datetime.date(2026, 6, 18),
        )
    }
    t = NOW - datetime.timedelta(days=30)
    open_ = fx.ledger(p[datetime.date(2026, 7, 9)], datetime.date(2026, 7, 9), created_at=t)
    scored = fx.ledger(
        p[datetime.date(2026, 7, 2)],
        datetime.date(2026, 7, 2),
        status="scored",
        actual=19730,
        created_at=t + datetime.timedelta(days=3),
    )
    missed = fx.ledger(
        p[datetime.date(2026, 6, 25)],
        datetime.date(2026, 6, 25),
        product="ron95",
        status="scored",
        actual=20900,
        miss=True,
        created_at=t + datetime.timedelta(days=2),
    )
    void = fx.ledger(
        p[datetime.date(2026, 6, 18)],
        datetime.date(2026, 6, 18),
        status="void",
        created_at=t + datetime.timedelta(days=1),
    )
    other = fx.prediction(fx.other_version, datetime.date(2026, 7, 2), experimental=True)
    foreign = fx.ledger(
        other, datetime.date(2026, 7, 2), model=fx.other_model, version=fx.other_version
    )
    return Ledger(scored, missed, open_, void, foreign)


async def test_the_ledger_lists_newest_period_first_with_its_calibration(fx: Fx) -> None:
    lines = _ledger(fx)
    order = [str(lines.open), str(lines.scored), str(lines.missed), str(lines.void)]
    async with client(fx) as api:
        listed = await api.get("/v1/forecast-ledger", headers=fx.headers("analyst"))
        assert listed.status_code == 200, listed.text
        assert [i["id"] for i in listed.json()["items"]] == order
        assert listed.json()["calibration"] is None

        seen: list[str] = []
        token = ""
        for _ in range(5):
            page = await api.get(
                "/v1/forecast-ledger",
                params={"page_size": 1, "page_token": token},
                headers=fx.headers("analyst"),
            )
            assert page.status_code == 200, page.text
            seen += [i["id"] for i in page.json()["items"]]
            token = page.json()["next_page_token"]
            if not token:
                break
        assert seen == order

        e5 = await api.get(
            "/v1/forecast-ledger",
            params={"product_code": "e5ron92", "status": "scored"},
            headers=fx.headers("analyst"),
        )
        assert [i["id"] for i in e5.json()["items"]] == [str(lines.scored)]
        missed = await api.get(
            "/v1/forecast-ledger", params={"miss": "true"}, headers=fx.headers("analyst")
        )
        assert [i["id"] for i in missed.json()["items"]] == [str(lines.missed)]
        ranged = await api.get(
            "/v1/forecast-ledger",
            params={"from": "2026-06-20", "to": "2026-07-05"},
            headers=fx.headers("analyst"),
        )
        assert [i["id"] for i in ranged.json()["items"]] == [str(lines.scored), str(lines.missed)]

        # BR-185: one model's ledger comes with its calibration over the last N periods.
        by_model = await api.get(
            "/v1/forecast-ledger",
            params={"forecast_model_id": str(fx.model)},
            headers=fx.headers("analyst"),
        )
        calibration = by_model.json()["calibration"]
        assert calibration["n_scored"] == 2 and calibration["miss_count"] == 1
        assert calibration["insufficient"] is True

        foreign = await api.get(
            "/v1/forecast-ledger",
            params={"forecast_model_id": str(fx.other_model)},
            headers=fx.headers("owner"),
        )
        assert foreign.status_code == 404
        denied = await api.get("/v1/forecast-ledger", headers=fx.headers("uploader"))
        assert denied.status_code == 403


async def test_a_ledger_line_is_read_in_its_data_space(fx: Fx) -> None:
    lines = _ledger(fx)
    async with client(fx) as api:
        got = await api.get(f"/v1/forecast-ledger/{lines.scored}", headers=fx.headers("analyst"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["status"] == "scored" and decimal.Decimal(body["actual"]) == 19730
        assert decimal.Decimal(body["abs_error"]) == 120 and body["in80"] is True
        assert body["quantiles"]["p50"] == 19850 and body["issued_via"] == "bulletin"
        hidden = await api.get(f"/v1/forecast-ledger/{lines.foreign}", headers=fx.headers("owner"))
        assert hidden.status_code == 404
        shown = await api.get(
            f"/v1/forecast-ledger/{lines.foreign}", headers=fx.headers("owner", fx.other)
        )
        assert shown.status_code == 200
        denied = await api.get(
            f"/v1/forecast-ledger/{lines.scored}", headers=fx.headers("uploader")
        )
        assert denied.status_code == 403


# AC-15-08-07
@pytest.mark.req("AC-15-08-07")
async def test_a_rescore_is_asked_of_ml_forecast(fx: Fx) -> None:
    lines = _ledger(fx)
    body = {"reason": "Bộ đính chính bản tin kỳ 02/7"}
    async with client(fx) as api:
        denied = await api.post(
            f"/v1/forecast-ledger/{lines.scored}:score", json=body, headers=fx.headers("analyst")
        )
        assert denied.status_code == 403
        blank = await api.post(
            f"/v1/forecast-ledger/{lines.scored}:score",
            json={"reason": " "},
            headers=fx.headers("steward"),
        )
        assert blank.status_code == 422
        asked = await api.post(
            f"/v1/forecast-ledger/{lines.scored}:score", json=body, headers=fx.headers("steward")
        )
        assert asked.status_code == 202, asked.text
        assert asked.headers["Location"] == f"/v1/forecast-ledger/{lines.scored}"
        assert asked.json()["status"] == "queued"
        event = dict(fx.events(lines.scored))["forecast.ledger.rescore_requested"]
        assert event["ledger_entry_id"] == str(lines.scored)
        assert event["forecast_model_id"] == str(fx.model)
        assert event["requested_by"] == str(fx.users["steward"])
        # The old score stays until ml-forecast writes the new one.
        assert (
            fx.one("SELECT actual FROM forecast.ledger_entry WHERE id = %s", lines.scored) == 19730
        )

        for line in (lines.open, lines.void):
            refused = await api.post(
                f"/v1/forecast-ledger/{line}:score", json=body, headers=fx.headers("steward")
            )
            assert refused.status_code == 409, refused.text
        hidden = await api.post(
            f"/v1/forecast-ledger/{lines.foreign}:score", json=body, headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# AC-15-08-03
@pytest.mark.req("AC-15-08-03")
async def test_calibration_over_the_last_scored_periods(fx: Fx) -> None:
    for i in range(12):
        period = datetime.date(2026, 1, 1) + datetime.timedelta(days=7 * i)
        prediction = fx.prediction(fx.published, period)
        fx.ledger(
            prediction,
            period,
            status="scored",
            actual=19730 + (500 if i == 11 else 0),
            miss=i == 11,
        )
    async with client(fx) as api:
        got = await api.get(
            f"/v1/forecast-models/{fx.model}/calibration", headers=fx.headers("analyst")
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["n_scored"] == 12 and body["last_n"] == 20 and body["insufficient"] is False
        assert body["miss_count"] == 1
        assert decimal.Decimal(body["coverage95"]) == decimal.Decimal("0.9167")
        assert decimal.Decimal(body["crps_naive"]) > 0 and body["mae"] is not None

        last = await api.get(
            f"/v1/forecast-models/{fx.model}/calibration",
            params={"last_n": 5},
            headers=fx.headers("analyst"),
        )
        # The newest five periods, the miss among them; fewer than 10 say little (BR-185).
        assert last.json()["n_scored"] == 5 and last.json()["miss_count"] == 1
        assert last.json()["insufficient"] is True
        other = await api.get(
            f"/v1/forecast-models/{fx.model}/calibration",
            params={"product_code": "ron95"},
            headers=fx.headers("analyst"),
        )
        assert other.json()["n_scored"] == 0 and other.json()["coverage80"] is None

        denied = await api.get(
            f"/v1/forecast-models/{fx.model}/calibration", headers=fx.headers("uploader")
        )
        assert denied.status_code == 403
        hidden = await api.get(
            f"/v1/forecast-models/{fx.other_model}/calibration", headers=fx.headers("owner")
        )
        assert hidden.status_code == 404


# AC-15-08-06
@pytest.mark.req("AC-15-08-06")
async def test_the_ledger_has_no_delete(fx: Fx, api_database: str) -> None:
    lines = _ledger(fx)
    async with client(fx) as api:
        # BR-185: no route removes a ledger line.
        gone = await api.delete(f"/v1/forecast-ledger/{lines.open}", headers=fx.headers("owner"))
        assert gone.status_code == 405
    parts = urlsplit(api_database)
    dsn = urlunsplit(
        (parts.scheme, f"svc_forecast:{PASSWORD}@{parts.hostname}:{parts.port}", parts.path, "", "")
    )
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(fx.org),))
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM forecast.ledger_entry WHERE id = %s", (str(lines.open),))
        conn.rollback()
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(fx.org),))
        # The forecast half never changes once written.
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "UPDATE forecast.ledger_entry SET point = 1 WHERE id = %s", (str(lines.open),)
            )
        conn.rollback()
    finally:
        conn.close()
    assert fx.one("SELECT point FROM forecast.ledger_entry WHERE id = %s", lines.open) == 19850
