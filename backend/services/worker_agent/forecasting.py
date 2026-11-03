"""Training a queued forecast job (UC-15-02, UC-15-03): the series, the fit, the version.

forecast.job.state_changed with status `queued` (the api queued it: a new model, a retrain, a
question with no current forecast):
1. the job moves to `preparing`, and its model is found: progress.model_id for a retrain, else the
   one model of the job's Data Space, metric, frequency and scope;
2. the metric's own series is read through the compiler and the executor, in place, as the metric
   owner (context.metric.owner_id) with the owner's row and column policies (invariant 13): the
   metric with the model's scope as filters, cut by its time dimension at the model's frequency,
   over every complete period before the cutoff. A table that declares when a row was recorded is
   read as of cutoff_at, so a row recorded later is not in it (BR-157). A period with no figure is
   0 only for an additive metric (a sum over the period of measures that add up over time); any
   other gap fails the job naming the periods. A last period the data stops part way through
   (semantic/coverage.py) is left out and named in the card's limits. Fewer than 24 periods with
   data fail the job in words;
3. `training`: the series goes to ml-forecast (libs/forecast_fit: FORECAST_SERVICE_URL, a bounded
   timeout); the library backtests every candidate against seasonal naive and keeps the best.
   Nothing better than seasonal naive fails the job with the library's reason;
4. `backtesting`: one transaction writes the draft model_version (its card holds the backtest
   table, the limits and scope_policy_hash), its backtest_run, `horizon` experimental predictions,
   submits the version to the metric owner by the api's own rule (libs/forecast_release: status
   pending_approval, forecast.model_version.submitted), and sets the job `ready`.

Each status change is its own forecast.job.state_changed, written in the transaction of the
change. A job that is no longer in the status a step expects (cancelled, or taken by a replayed
event) is left as it is: the step writes nothing. The fitted params are also written to MinIO
(da-forecast-models, forecast/{org}/{version}.json, artifact_key); without a store the version
keeps them in model_version.params alone and artifact_key stays NULL.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import logging
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from libs import forecast_release as release
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context
from libs.dataagent_db.models import forecast as fc
from libs.forecast_fit import Candidate, FitResult, Point
from libs.forecast_fit.service import TrainerUnavailable, fit_series
from services.worker_agent import questions, storage
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.scope import active_policies, can_ask_data_space
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.coverage import from_rows
from services.worker_agent.semantic.loader import SpaceLayer, load

logger = logging.getLogger(__name__)

ROLE = "svc_forecast"
BUCKET = os.environ.get("FORECAST_MODEL_BUCKET", "da-forecast-models")
MIN_POINTS = 24
TERMINAL = ("ready", "failed", "cancelled")
# The owner's own reading of the series (query/scope.PERSON_CALLERS), as the digest reads a
# metric for its owner: a policy narrowing what the agent reads does not narrow it.
CALLER: Literal["ad_hoc"] = "ad_hoc"
# The library's interval method → prediction.interval_method.
INTERVALS = {"model": "parametric", "conformal": "conformal"}
# The series starts at its first period with data: the read starts before any.
EARLIEST = dt.date(1900, 1, 1)

LIMITS = (
    "Mô hình chỉ học từ chuỗi của chính chỉ số, không dùng biến ngoại sinh; nó không biết trước "
    "sự kiện chưa có trong quá khứ (khuyến mãi mới, thay đổi chính sách, cú sốc thị trường). "
    "Khoảng dự báo giả định sai số tương lai giống sai số trong backtest. Kết quả chỉ là thử "
    "nghiệm cho đến khi người phụ trách chỉ số duyệt."
)
NOTHING_BEATS = "Không mô hình nào tốt hơn cách đoán theo mùa (seasonal naive)"
SYSTEM_ERROR = "Huấn luyện dừng vì lỗi hệ thống; hãy chạy lại sau."


class Refused(Exception):
    """The job cannot be trained; the message is what the person reads."""


@dataclass(frozen=True)
class Series:
    points: list[Point]
    query_run_id: uuid.UUID
    scope_policy_hash: str
    point_in_time: str  # "knowledge_time" (as of cutoff_at) or "period_end"
    # The last period read, left out because its data stops part way through it.
    dropped: dt.date | None = None


def _period_start(day: dt.date, frequency: str) -> dt.date:
    if frequency == "quarter":
        return day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
    return day.replace(day=1)


def _next(period: dt.date, frequency: str) -> dt.date:
    months = 3 if frequency == "quarter" else 1
    y, m = divmod(period.month - 1 + months, 12)
    return dt.date(period.year + y, m + 1, 1)


def _label(period: dt.date, frequency: str) -> str:
    if frequency == "quarter":
        return f"{period.year}-Q{(period.month - 1) // 3 + 1}"
    return f"{period:%Y-%m}"


def _period_of(label: Any, frequency: str) -> dt.date:
    """'2026-09' or '2026-Q3' (the compiler's labels) → the period's first day."""
    text = str(label)
    try:
        if frequency == "quarter":
            year, quarter = text.split("-Q")
            return dt.date(int(year), (int(quarter) - 1) * 3 + 1, 1)
        year, month = text[:7].split("-")
        return dt.date(int(year), int(month), 1)
    except ValueError as exc:
        raise Refused(f"Kỳ {text} của chuỗi không đọc được.") from exc


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(decimal.Decimal(str(value)))
    except (decimal.InvalidOperation, ValueError):
        return None
    return number if math.isfinite(number) else None


def additive(version: c.MetricVersion, layer: c.Layer) -> bool:
    """A metric whose periods add up: summed over the period, and only measures that add up over
    time (SUM or COUNT, additivity full) joined by + and -. A ratio, an average or a balance is
    not: a missing month of those is unknown, never 0."""
    if version.period_semantics != "sum_over_period":
        return False
    try:
        tree = parse_one(version.expression)
    except ParseError:
        return False
    allowed = (exp.Column, exp.Identifier, exp.Add, exp.Sub, exp.Paren)
    if not all(isinstance(node, allowed) for node in tree.find_all(exp.Expression)):
        return False
    for column in tree.find_all(exp.Column):
        measure = layer.measures.get(column.name)
        if measure is None or measure.additivity != "full":
            return False
        try:
            aggregate = parse_one(measure.expression)
        except ParseError:
            return False
        if not isinstance(aggregate, exp.Sum | exp.Count):
            return False
    return True


def points_of(
    rows: list[list[Any]], frequency: str, *, fill_zero: bool, metric_code: str
) -> list[Point]:
    """The rows (label, value) as a series in period order, every period from the first to the
    last with data. A gap is 0 for an additive metric and refuses the series otherwise."""
    present: dict[dt.date, float] = {}
    for row in rows:
        value = _number(row[-1])
        if value is not None:
            present[_period_of(row[0], frequency)] = value
    if not present:
        raise Refused("Chỉ số chưa có số liệu nào trước thời điểm cắt.")
    if len(present) < MIN_POINTS:
        raise Refused(f"Cần ít nhất {MIN_POINTS} kỳ có dữ liệu; chuỗi này có {len(present)}")
    period, last = min(present), max(present)
    points: list[Point] = []
    missing: list[dt.date] = []
    while period <= last:
        if period not in present:
            missing.append(period)
        points.append(Point(period, present.get(period, 0.0)))
        period = _next(period, frequency)
    if missing and not fill_zero:
        names = ", ".join(_label(p, frequency) for p in missing)
        raise Refused(
            f"Chuỗi thiếu số liệu ở kỳ {names}; chỉ số {metric_code} không cộng dồn theo thời "
            "gian nên không điền 0 cho kỳ trống được. Hãy bổ sung số liệu rồi chạy lại."
        )
    return points


def policy_hash(policies: list[Any]) -> str:
    """sha256 of the owner's active policies on the series' datasets, by id and version: a version
    trained under other policies has another hash."""
    pairs = sorted([str(p.id), int(p.version)] for p in policies)
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()


@dataclass(frozen=True)
class _Job:
    id: uuid.UUID
    org_id: uuid.UUID
    data_space_id: uuid.UUID
    requested_by: uuid.UUID
    metric_code: str
    scope: dict[str, Any]
    frequency: str
    horizon: int
    cutoff_at: dt.datetime
    progress: dict[str, Any]


def _snapshot(row: fc.ForecastJob) -> _Job:
    return _Job(
        row.id,
        row.org_id,
        row.data_space_id,
        row.requested_by,
        row.metric_code,
        dict(row.scope or {}),
        row.frequency,
        row.horizon,
        row.cutoff_at,
        dict(row.progress or {}),
    )


def _set_status(row: fc.ForecastJob, status: str, **progress: Any) -> str:
    previous = row.status
    row.status = status
    row.progress = {**(row.progress or {}), **progress, "step": status}
    return previous


async def _locked_job(session: AsyncSession, job_id: uuid.UUID) -> fc.ForecastJob | None:
    found: fc.ForecastJob | None = await session.scalar(
        select(fc.ForecastJob).where(fc.ForecastJob.id == job_id).with_for_update()
    )
    return found


async def _move(org: uuid.UUID, job_id: uuid.UUID, frm: str, to: str) -> _Job | None:
    """The job from `frm` to `to` with its event; None when it is no longer in `frm`."""
    async with scoped_session(ROLE, org) as session:
        row = await _locked_job(session, job_id)
        if row is None or row.status != frm:
            return None
        previous = _set_status(row, to)
        release.job_changed(session, row, previous)
        return _snapshot(row)


async def _fail(org: uuid.UUID, job_id: uuid.UUID, message: str) -> None:
    async with scoped_session(ROLE, org) as session:
        row = await _locked_job(session, job_id)
        if row is None or row.status in TERMINAL:
            return
        previous = _set_status(row, "failed", error=message)
        row.finished_at = release.now()
        release.job_changed(session, row, previous)


async def _model(job: _Job) -> fc.ForecastModel:
    async with scoped_session(ROLE, job.org_id) as session:
        query = select(fc.ForecastModel).where(
            fc.ForecastModel.org_id == job.org_id,
            fc.ForecastModel.data_space_id == job.data_space_id,
        )
        model_id = job.progress.get("model_id")
        if model_id:
            query = query.where(fc.ForecastModel.id == uuid.UUID(str(model_id)))
        else:
            query = query.where(
                fc.ForecastModel.metric_code == job.metric_code,
                fc.ForecastModel.frequency == job.frequency,
                fc.ForecastModel.scope == job.scope,
            )
        model = await session.scalar(query)
    if model is None:
        raise Refused(
            f"Chưa có mô hình dự báo cho chỉ số {job.metric_code} theo phạm vi và tần suất này."
        )
    if model.status == "retired":
        raise Refused("Mô hình dự báo đã ngừng dùng nên không huấn luyện lại.")
    if model.frequency not in ("month", "quarter"):
        raise Refused("Hiện chỉ huấn luyện được chuỗi theo tháng hoặc theo quý.")
    return model


def _metric_version(layer: SpaceLayer, code: str) -> c.MetricVersion:
    for metric_id, version in layer.versions.items():
        if version.metric_code == code:
            if metric_id in layer.blocked_metric_ids:
                raise Refused(f"Chính sách truy cập đang chặn chỉ số {code}.")
            if not version.certified:
                raise Refused(f"Chỉ số {code} chưa được chứng nhận nên không huấn luyện được.")
            return version
    raise Refused(f"Không tìm thấy chỉ số {code} đang hiệu lực trong Data Space này.")


def _base_tables(version: c.MetricVersion, layer: c.Layer) -> list[c.Table]:
    """The tables the metric's measures are read on."""
    try:
        tree = parse_one(version.expression)
    except ParseError:
        return []
    out: list[c.Table] = []
    for column in tree.find_all(exp.Column):
        measure = layer.measures.get(column.name)
        table = layer.entity_tables.get(measure.entity_id) if measure else None
        if table is not None and table not in out:
            out.append(table)
    return out


def _filters(scope: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        str(code): [str(v) for v in (values if isinstance(values, list) else [values])]
        for code, values in scope.items()
    }


async def read_series(job: _Job, model: fc.ForecastModel) -> Series:
    """The metric's series as its owner reads it, as of the job's cutoff (module docstring, 2)."""
    org, space = job.org_id, job.data_space_id
    async with scoped_session("svc_qa", org) as session:
        owner = await session.scalar(
            select(context.Metric.owner_id).where(
                context.Metric.data_space_id == space, context.Metric.code == job.metric_code
            )
        )
    if owner is None:
        raise Refused(f"Không tìm thấy chỉ số {job.metric_code} trong Data Space này.")
    # Complete periods only: the cutoff's own period is not over.
    end = _period_start(job.cutoff_at.astimezone(dt.UTC).date(), model.frequency)
    async with scoped_session("svc_qa", org, owner) as session:
        if not await can_ask_data_space(session, data_space_id=space, principal_id=owner):
            raise Refused("Người phụ trách chỉ số không có quyền đọc Data Space này.")
        layer = await load(session, space)
        version = _metric_version(layer, job.metric_code)
        bases = _base_tables(version, layer.layer)
        # As of the cutoff where every table says when its rows were recorded; elsewhere a row
        # of a period before the cutoff is taken as known by then.
        as_of = bool(bases) and all(
            t.history_kind in {"temporal", "scd2"} and t.knowledge_time_col for t in bases
        )
        try:
            time_dim = c.time_dimension_of(version, layer.layer)
            if time_dim is None:
                raise c.CompileError("NO_DATA_FOR_PERIOD", "the metric has no time dimension")
            request = c.Request(
                version,
                group_by=[time_dim.code],
                filters=_filters(model.scope or {}),
                period=c.Period(EARLIEST, end),
                grain=model.frequency,
                knowledge_at=job.cutoff_at if as_of else None,
            )
            compiled = c.compile_metric(request, layer.layer, dialect="postgres")
            dataset = layer.datasets[compiled.dataset_ids[0]]
            source = (
                questions._internal_workspace_source(space, org)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.status not in questions.QUERYABLE:
                raise Refused("Nguồn dữ liệu của chỉ số đang không truy vấn được.")
            compiled = c.compile_metric(
                request, layer.layer, dialect=questions.dialect_for_source(source.kind)
            )
        except (c.CompileError, KeyError) as exc:
            code = getattr(exc, "code", "CONTRACT_MISMATCH")
            raise Refused(
                f"Không dựng được truy vấn chuỗi của chỉ số {job.metric_code} ({code})."
            ) from exc
        datasets = [layer.datasets[i] for i in compiled.dataset_ids if i in layer.datasets]
    async with scoped_session("svc_query", org, owner) as session:
        policies = await active_policies(
            session, principal_id=owner, datasets=datasets, caller_kind=CALLER
        )
    outcome = await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=owner,
        caller_kind=CALLER,
        caller_id=job.id,
        source=source,
        sql=compiled.sql,
        secrets=questions.secrets(),
        metric_version_ids=[version.id],
    )
    if not outcome.ok:
        logger.warning("forecast | series of job %s unread: %s", job.id, outcome.error)
        raise Refused("Không đọc được chuỗi của chỉ số từ nguồn dữ liệu.")
    if outcome.truncated:
        raise Refused("Chuỗi của chỉ số dài quá giới hạn một lần đọc.")
    # A last period the books stop part way through (coverage's own test, AGC-10) would bend
    # every forecast down: it is left out, and the horizon starts after the last whole period.
    rows = list(outcome.rows)
    coverage = from_rows(rows, model.frequency)
    dropped = coverage.last if coverage.partial_last else None
    if dropped is not None:
        rows = [r for r in rows if _period_of(r[0], model.frequency) != dropped]
    points = points_of(
        rows,
        model.frequency,
        fill_zero=additive(version, layer.layer),
        metric_code=job.metric_code,
    )
    return Series(
        points,
        outcome.query_run_id,
        policy_hash(policies),
        "knowledge_time" if as_of else "period_end",
        dropped,
    )


def _figures(candidate: Candidate) -> dict[str, float | None]:
    # sMAPE as a share like the coverages: the screens read both as shares (lib/v1/forecast.ts).
    return {
        "mase": candidate.mase,
        "smape": None if candidate.smape is None else candidate.smape / 100,
        "coverage80": candidate.coverage80,
        "coverage95": candidate.coverage95,
        "in_sample_mase": candidate.in_sample_mase,
    }


def _limits(series: Series, frequency: str) -> str:
    if series.dropped is None:
        return LIMITS
    return f"{LIMITS} Bỏ kỳ {_label(series.dropped, frequency)} vì dữ liệu chưa đủ kỳ."


def _decimal(value: float) -> decimal.Decimal:
    return decimal.Decimal(str(value))


async def _artifact(org: uuid.UUID, version_id: uuid.UUID, params: dict[str, Any]) -> str | None:
    key = f"forecast/{org}/{version_id}.json"
    body = json.dumps(params, sort_keys=True, allow_nan=False).encode()
    try:
        await storage.store().put(BUCKET, key, body, "application/json")
    except storage.StoreUnavailable as exc:
        # The params are in model_version.params either way; the object is their copy.
        logger.warning("forecast | artifact of version %s not written: %s", version_id, exc)
        return None
    return key


async def _write(job: _Job, model_id: uuid.UUID, series: Series, fit: FitResult) -> None:
    """The version, its backtest and predictions, its submission and the job ready, at once."""
    chosen = next((x for x in fit.candidates if x.algorithm == fit.chosen), None)
    if chosen is None or chosen.mase is None or chosen.mase >= 1:
        raise Refused(NOTHING_BEATS)
    interval = INTERVALS.get(fit.interval_method or "")
    if interval is None:
        raise Refused("Kết quả dự báo không nói khoảng dự báo được tính thế nào.")
    if len(fit.predictions) != job.horizon:
        raise Refused(
            f"Dịch vụ dự báo trả {len(fit.predictions)} kỳ thay vì {job.horizon} kỳ đã hỏi."
        )
    baseline = next((x for x in fit.candidates if x.algorithm == "seasonal_naive"), None)
    in_sample = chosen.in_sample_mase
    version_id = uuid7()
    artifact_key = await _artifact(job.org_id, version_id, fit.params)
    table = [{"algorithm": x.algorithm, **_figures(x)} for x in fit.candidates]
    now = release.now()
    async with scoped_session(ROLE, job.org_id) as session:
        row = await _locked_job(session, job.id)
        if row is None or row.status != "backtesting":
            return
        # Locked: two jobs of one model must not take the same version number.
        model = await session.scalar(
            select(fc.ForecastModel).where(fc.ForecastModel.id == model_id).with_for_update()
        )
        if model is None or model.status == "retired":
            raise Refused("Mô hình dự báo đã ngừng dùng nên không nhận phiên bản mới.")
        latest = await session.scalar(
            select(func.max(fc.ModelVersion.version)).where(fc.ModelVersion.model_id == model.id)
        )
        version = fc.ModelVersion(
            id=version_id,
            org_id=job.org_id,
            model_id=model.id,
            version=(latest or 0) + 1,
            algorithm=chosen.algorithm,
            params=fit.params,
            training_from=series.points[0].period,
            training_to=series.points[-1].period,
            data_snapshot_refs=[str(series.query_run_id)],
            n_points=len(series.points),
            status="draft",
            model_card={
                "backtest": table,
                "limits": _limits(series, model.frequency),
                "scope_policy_hash": series.scope_policy_hash,
                "scope": model.scope or {},
                "data_as_of": job.cutoff_at.isoformat(),
                "point_in_time": series.point_in_time,
                "interval_method": interval,
            },
            artifact_key=artifact_key,
            created_by=job.requested_by,
            updated_by=job.requested_by,
        )
        session.add(version)
        await session.flush()
        session.add(
            fc.BacktestRun(
                id=uuid7(),
                org_id=job.org_id,
                model_version_id=version_id,
                frequency=model.frequency,
                cutoffs=list(fit.cutoffs),
                horizon=job.horizon,
                metrics={**_figures(chosen), "interval_method": interval},
                baseline_metrics=_figures(baseline) if baseline else {"mase": 1.0},
                mase=_decimal(round(chosen.mase, 4)),
                in_sample_mase=(
                    _decimal(round(in_sample, 4))
                    if in_sample is not None and round(in_sample, 4) > 0
                    else None
                ),
                candidates=table,
                started_at=now,
                finished_at=now,
            )
        )
        for forecast in fit.predictions:
            session.add(
                fc.Prediction(
                    id=uuid7(),
                    org_id=job.org_id,
                    model_version_id=version_id,
                    job_id=job.id,
                    cutoff_at=job.cutoff_at,
                    period=forecast.period,
                    point=_decimal(forecast.point),
                    lo80=_decimal(forecast.lo80),
                    hi80=_decimal(forecast.hi80),
                    lo95=_decimal(forecast.lo95),
                    hi95=_decimal(forecast.hi95),
                    interval_method=interval,
                    # BR-158: a version nobody approved yet forecasts experimentally.
                    is_experimental=True,
                )
            )
        await session.flush()
        try:
            await release.submit(session, version, model, by=job.requested_by, note=None)
        except release.SubmitRefused as exc:
            raise Refused("Phiên bản mô hình không đủ điều kiện gửi duyệt (BR-158).") from exc
        row.model_version_id = version_id
        previous = _set_status(row, "ready")
        row.finished_at = release.now()
        release.job_changed(session, row, previous)


async def _train(org: uuid.UUID, job: _Job) -> None:
    model = await _model(job)
    series = await read_series(job, model)
    if await _move(org, job.id, "preparing", "training") is None:
        return
    # _model kept month and quarter only.
    frequency: Literal["month", "quarter"] = "quarter" if model.frequency == "quarter" else "month"
    try:
        fit = await fit_series(series.points, frequency=frequency, horizon=job.horizon)
    except ValueError as exc:
        # The library's own refusal of the series, in words.
        raise Refused(str(exc)) from exc
    if fit.chosen is None:
        raise Refused(fit.reason or NOTHING_BEATS)
    if await _move(org, job.id, "training", "backtesting") is None:
        return
    await _write(job, model.id, series, fit)


async def on_job(event: Event) -> None:
    """Trains a queued job; any other status, or a job no longer queued, is not its event."""
    if event.payload.get("status") != "queued":
        return
    job_id = uuid.UUID(str(event.payload.get("job_id") or event.aggregate_id))
    job = await _move(event.org_id, job_id, "queued", "preparing")
    if job is None:
        return
    try:
        await _train(event.org_id, job)
    except (Refused, TrainerUnavailable) as exc:
        await _fail(event.org_id, job_id, str(exc))
    except Exception:  # the job ends in words, never stuck in a step
        logger.exception("forecast | job %s failed", job_id)
        await _fail(event.org_id, job_id, SYSTEM_ERROR)


HANDLERS: dict[str, Handler] = {release.JOB_CHANGED: on_job}
