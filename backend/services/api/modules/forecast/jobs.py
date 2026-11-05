"""Forecast jobs (UC-15-01): ask for a forecast, follow it, cancel it, read its distribution.

The API never computes a forecast. A question with a still-valid forecast of a released model is
answered at once (BR-159); otherwise a forecast_job is queued with cutoff_at = now, so ml-forecast
uses only data known before the request (BR-157, point-in-time by available_at), and the 202
points at the job. ml-forecast reads forecast.job.state_changed from the outbox.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs import forecast_release as release
from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.kernel.sse import Event, event_stream, last_event_id
from services.api.modules.forecast import rules, store
from services.api.modules.forecast.schemas import (
    BacktestRow,
    CanCreateCard,
    DistributionItem,
    DistributionView,
    ForecastAccepted,
    ForecastCancel,
    ForecastCreate,
    ForecastDetail,
    ForecastJobView,
    ForecastStat,
    ForecastVariable,
    ForecastWorkbench,
    PredictionView,
)
from services.api.modules.forecast.store import ROLE

router = APIRouter()

JobId = Annotated[uuid.UUID, Path(alias="id")]
_NO_CANCEL = ForecastCancel()


def new_job(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    metric_code: str,
    frequency: str,
    horizon: int,
    scope: dict[str, Any],
    progress: dict[str, Any],
    question_id: uuid.UUID | None = None,
    assumptions: list[dict[str, Any]] | None = None,
    notify_channels: list[str] | None = None,
    model_version_id: uuid.UUID | None = None,
) -> fc.ForecastJob:
    """A queued job and its forecast.job.state_changed event, cut at now (BR-157)."""
    cutoff = store.now()
    row = fc.ForecastJob(
        id=uuid7(),
        org_id=principal.org_id,
        data_space_id=data_space,
        question_id=question_id,
        requested_by=principal.user_id,
        metric_code=metric_code,
        scope=scope,
        assumptions=assumptions or [],
        frequency=frequency,
        horizon=horizon,
        cutoff_at=cutoff,
        status="queued",
        eta_at=cutoff + rules.ETA[frequency],
        notify_channels=notify_channels or [],
        progress={"step": "queued", **progress},
        model_version_id=model_version_id,
        created_by=principal.user_id,
    )
    session.add(row)
    release.job_changed(session, row, None)
    return row


def accepted(response: Response, row: fc.ForecastJob) -> ForecastAccepted:
    response.status_code = 202
    response.headers["Location"] = f"/v1/forecasts/{row.id}"
    return ForecastAccepted(
        id=row.id, status=row.status, poll_after_ms=rules.POLL_AFTER_MS, eta_at=row.eta_at
    )


async def _base_predictions(session: AsyncSession, job_id: uuid.UUID) -> list[fc.Prediction]:
    return list(
        (
            await session.scalars(
                select(fc.Prediction)
                .where(fc.Prediction.job_id == job_id, fc.Prediction.scenario_id.is_(None))
                .order_by(fc.Prediction.period, fc.Prediction.model_version_id)
            )
        ).all()
    )


async def _detail(session: AsyncSession, row: fc.ForecastJob) -> ForecastDetail:
    out = ForecastDetail(**ForecastJobView.model_validate(row).model_dump())
    if row.status != "ready" or row.model_version_id is None:
        return out
    predictions = await _base_predictions(session, row.id)
    version = await session.get(fc.ModelVersion, row.model_version_id)
    out.predictions = [PredictionView.model_validate(p) for p in predictions[: row.horizon]]
    out.labels = rules.labels(
        version.status if version else None,
        version.algorithm if version else None,
        any(p.is_experimental for p in predictions),
    )
    out.data_as_of = row.cutoff_at
    return out


async def _current_forecast(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, body: ForecastCreate
) -> fc.ForecastJob | None:
    """A ready job of the model's released version that still answers the question (BR-159)."""
    released = await session.scalar(
        select(fc.ForecastModel).where(
            fc.ForecastModel.org_id == principal.org_id,
            fc.ForecastModel.data_space_id == data_space,
            fc.ForecastModel.metric_code == body.metric_code,
            fc.ForecastModel.frequency == body.frequency,
            fc.ForecastModel.scope == body.scope,
            fc.ForecastModel.status != "retired",
            fc.ForecastModel.current_version_id.is_not(None),
        )
    )
    if released is None:
        return None
    assumptions = [a.model_dump(mode="json") for a in body.assumptions]
    candidate = await session.scalar(
        select(fc.ForecastJob)
        .where(
            fc.ForecastJob.org_id == principal.org_id,
            fc.ForecastJob.data_space_id == data_space,
            fc.ForecastJob.metric_code == body.metric_code,
            fc.ForecastJob.frequency == body.frequency,
            fc.ForecastJob.scope == body.scope,
            fc.ForecastJob.assumptions == assumptions,
            fc.ForecastJob.status == "ready",
            fc.ForecastJob.model_version_id == released.current_version_id,
            fc.ForecastJob.horizon >= body.horizon,
        )
        .order_by(fc.ForecastJob.cutoff_at.desc())
        .limit(1)
    )
    if candidate is None:
        return None
    periods = [p.period for p in await _base_predictions(session, candidate.id)]
    if not rules.forecast_is_current(periods, store.now().date()):
        return None
    return candidate


@router.post(
    "/v1/forecasts",
    response_model=ForecastDetail | ForecastAccepted,
    **endpoint("POST", "/v1/forecasts", operation_id="createForecast"),
)
async def create_forecast(
    body: ForecastCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ForecastDetail | ForecastAccepted:
    async with transaction(ROLE, principal) as session:
        current = await _current_forecast(session, principal, data_space, body)
        if current is not None:
            # 200: the released forecast, trimmed to the asked horizon.
            detail = await _detail(session, current)
            detail.predictions = detail.predictions[: body.horizon]
            return detail
        row = new_job(
            session,
            principal,
            data_space,
            metric_code=body.metric_code,
            frequency=body.frequency,
            horizon=body.horizon,
            scope=body.scope,
            progress={"purpose": "forecast"},
            question_id=body.question_id,
            assumptions=[a.model_dump(mode="json") for a in body.assumptions],
            notify_channels=body.notify_channels,
        )
        await session.flush()
        return accepted(response, row)


@router.get(
    "/v1/forecasts/{id}",
    response_model=ForecastDetail,
    **endpoint("GET", "/v1/forecasts/{id}", operation_id="getForecast"),
)
async def get_forecast(
    job_id: JobId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ForecastDetail:
    async with transaction(ROLE, principal) as session:
        row = await store.job(session, job_id, principal, data_space)
        return await _detail(session, row)


# The studio screen reads this shape directly (frontend getForecastWorkbench). Keep the words
# and the rows in step with that reader: it no longer rebuilds them once this route answers.
_FREQUENCY = {"day": "ngày", "week": "tuần", "month": "tháng", "quarter": "quý"}
_ALGORITHM = {
    "naive": "Naive",
    "seasonal_naive": "Seasonal naive",
    "ets": "ETS",
    "theta": "Theta",
    "arima": "ARIMA",
    "sarimax": "SARIMAX",
    "lightgbm": "LightGBM",
    "ensemble": "Ensemble",
    "formula_mc": "formula_mc",
}
_FUTURE = {
    "declared": "Giả định",
    "forecast": "Dự báo riêng",
    "own_forecast": "Dự báo riêng",
    "known": "Biết trước",
    "calendar": "Biết trước",
}
_SUBMITTED = frozenset({"pending_approval", "published", "degraded", "retired", "rejected"})
_VARIABLES_NOTE = (
    "Mọi biến căn theo thời điểm ghi nhận; biến chỉ có sau thời điểm cắt bị loại tự động (BR-157)."
)


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int | float | decimal.Decimal):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return None if number != number else number  # NaN


def _vi(value: float, places: int) -> str:
    quant = decimal.Decimal(str(value)).quantize(decimal.Decimal(10) ** -places)
    return f"{quant:.{places}f}".replace(".", ",")


def _share(value: object) -> str:
    number = _num(value)
    if number is None:
        return "—"
    text = _vi(number * 100, 1)
    if text.endswith(",0"):
        text = text[:-2]
    return f"{text}%"


def _date_vn(value: datetime.date | datetime.datetime) -> str:
    day = value.date() if isinstance(value, datetime.datetime) else value
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _variables(features: object) -> list[ForecastVariable]:
    if not isinstance(features, list):
        return []
    out: list[ForecastVariable] = []
    for raw in features:
        feature = _mapping(raw)
        name = feature.get("name")
        if not isinstance(name, str):
            continue
        future = feature.get("future_assumption")
        future_text = future if isinstance(future, str) else ""
        lag = feature.get("lag")
        source = feature.get("source")
        out.append(
            ForecastVariable(
                name=name,
                source=source if isinstance(source, str) else "",
                lag_text="—" if lag is None else str(lag),
                correlation_text="—",
                future_value_text=_FUTURE.get(future_text, future_text) if future_text else "—",
                status="used",
                status_text="Dùng",
            )
        )
    return out


def _backtest_rows(run: fc.BacktestRun, selected: str) -> list[BacktestRow]:
    base = _mapping(run.baseline_metrics)
    rows = [
        BacktestRow(
            model="Seasonal naive",
            mase=_num(base.get("mase")) if base.get("mase") is not None else 1.0,
            smape_text=_share(base.get("smape")),
            cover80_text=_share(base.get("coverage80")),
            cover95_text=_share(base.get("coverage95")),
            role="baseline",
        )
    ]
    candidates = run.candidates if isinstance(run.candidates, list) else []
    for raw in candidates:
        candidate = _mapping(raw)
        code = next(
            (
                candidate[key]
                for key in ("algorithm", "model", "name")
                if isinstance(candidate.get(key), str)
            ),
            None,
        )
        if not isinstance(code, str) or code == "seasonal_naive":
            continue
        rows.append(
            BacktestRow(
                model=_ALGORITHM.get(code, code),
                mase=_num(candidate.get("mase")),
                smape_text=_share(candidate.get("smape")),
                cover80_text=_share(candidate.get("coverage80")),
                cover95_text=_share(candidate.get("coverage95")),
                role="selected" if code == selected else "candidate",
            )
        )
    if not any(row.role == "selected" for row in rows):
        rows.append(
            BacktestRow(
                model=_ALGORITHM.get(selected, selected),
                mase=_num(run.mase),
                smape_text="—",
                cover80_text="—",
                cover95_text="—",
                role="selected",
            )
        )
    return rows


def _workbench(
    job: fc.ForecastJob, version: fc.ModelVersion | None, run: fc.BacktestRun | None
) -> ForecastWorkbench:
    mase = _num(run.mase) if run is not None else None
    parts = [
        f"Chuỗi {_FREQUENCY.get(job.frequency, job.frequency)}",
        f"cắt tại {_date_vn(job.cutoff_at)}",
        f"tầm {job.horizon} kỳ",
    ]
    if run is not None:
        parts.append(f"backtest trên {len(run.cutoffs)} điểm cắt")
    if version is None or run is None:
        card = CanCreateCard(
            allowed=False,
            reason="Việc dự báo chưa có phiên bản mô hình đã qua backtest.",
        )
    elif mase is None or mase >= 1:
        card = CanCreateCard(
            allowed=False,
            reason="Không mô hình nào thắng seasonal naive (MASE < 1), nên không có thẻ để duyệt.",
        )
    elif version.status == "draft":
        card = CanCreateCard(allowed=True, reason=None)
    else:
        card = CanCreateCard(allowed=False, reason="Thẻ mô hình đã được gửi duyệt.")
    stats: list[ForecastStat] = []
    if version is not None:
        points = "—" if version.n_points is None else f"{version.n_points} kỳ"
        tone: Literal["success", "warning", "error", "neutral", "default"] = (
            "success" if mase is not None and mase < 1 else "warning"
        )
        stats = [
            ForecastStat(
                label="Chuỗi huấn luyện",
                value=points,
                note=f"{_date_vn(version.training_from)} - {_date_vn(version.training_to)}",
                tone="default",
            ),
            ForecastStat(
                label="Biến ngoại sinh",
                value=str(version.n_exog),
                note="tối đa min(8, số kỳ / 12) (BR-172)",
                tone="neutral",
            ),
            ForecastStat(
                label="MASE backtest",
                value="—" if mase is None else _vi(mase, 2),
                note="phát hành khi < 1 (BR-158)",
                tone=tone,
            ),
        ]
    note = ""
    if mase is not None:
        note = (
            f"MASE tổng hợp {_vi(mase, 2)} trên mọi điểm cắt và tầm; "
            "khoảng dự báo theo sai số backtest từng bước tầm."
        )
    return ForecastWorkbench(
        job_id=job.id,
        metric_name=job.metric_code,
        summary_text=" · ".join(parts),
        series_stats=stats,
        variables=_variables(version.features) if version is not None else [],
        variables_note=_VARIABLES_NOTE,
        backtest=_backtest_rows(run, version.algorithm) if version is not None and run else [],
        backtest_note=note,
        version_id=version.id if version is not None else None,
        forecast_model_id=version.model_id if version is not None else None,
        next_version_label=f"v{version.version}" if version is not None else "",
        can_create_card=card,
        model_id=(
            version.model_id if version is not None and version.status in _SUBMITTED else None
        ),
    )


@router.get(
    "/v1/forecast-jobs/{id}/workbench",
    response_model=ForecastWorkbench,
    **endpoint("GET", "/v1/forecast-jobs/{id}/workbench", operation_id="getForecastWorkbench"),
)
async def get_workbench(
    job_id: JobId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ForecastWorkbench:
    async with transaction(ROLE, principal) as session:
        row = await store.job(session, job_id, principal, data_space)
        version = (
            await session.get(fc.ModelVersion, row.model_version_id)
            if row.model_version_id is not None
            else None
        )
        run = await release.latest_backtest(session, version) if version is not None else None
    return _workbench(row, version, run)


def _events(row: fc.ForecastJob, detail: ForecastDetail) -> list[Event]:
    """The job so far as A1.6 events; numbered, so Last-Event-ID resumes after one."""
    ref = {"job_id": str(row.id)}
    events: list[tuple[str, dict[str, Any]]] = [
        ("progress", {**ref, "status": row.status, "progress": row.progress}),
    ]
    if row.eta_at is not None:
        events.append(("eta_changed", {**ref, "eta_at": row.eta_at.isoformat()}))
    if row.status == "waiting_human":
        events.append(("waiting_human", {**ref, "progress": row.progress}))
    if row.status == "ready":
        events.append(
            (
                "result",
                {
                    **ref,
                    "model_version_id": str(row.model_version_id),
                    "labels": detail.labels,
                    "predictions": [p.model_dump(mode="json") for p in detail.predictions],
                },
            )
        )
    if row.status == "failed":
        events.append(("error", {**ref, "error": (row.progress or {}).get("error")}))
    if row.status in rules.TERMINAL_JOB:
        events.append(("done", {**ref, "status": row.status}))
    return [Event(str(i), name, data) for i, (name, data) in enumerate(events, start=1)]


@router.get(
    "/v1/forecasts/{id}/events",
    **endpoint("GET", "/v1/forecasts/{id}/events", operation_id="streamForecastEvents"),
)
async def stream_forecast_events(
    job_id: JobId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    last_id: Annotated[str | None, Depends(last_event_id)],
) -> Response:
    async with transaction(ROLE, principal) as session:
        row = await store.job(session, job_id, principal, data_space)
        events = _events(row, await _detail(session, row))
    if last_id:
        try:
            after = int(last_id)
        except ValueError as exc:
            raise Problem(400, "invalid_request", "Last-Event-ID must be numeric.") from exc
        events = [e for e in events if int(e.id) > after]

    async def produce() -> AsyncIterator[Event]:
        for event in events:
            yield event

    return event_stream(produce())


@router.post(
    "/v1/forecasts/{id}:cancel",
    response_model=ForecastJobView,
    **endpoint("POST", "/v1/forecasts/{id}:cancel", operation_id="cancelForecast"),
)
async def cancel_forecast(
    job_id: JobId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[ForecastCancel, Body()] = _NO_CANCEL,
) -> ForecastJobView:
    async with transaction(ROLE, principal) as session:
        row = await store.job(session, job_id, principal, data_space, lock=True)
        if row.requested_by != principal.user_id and "steward" not in principal.capabilities:
            raise Problem(403, "forbidden", "Only whoever asked for the forecast can cancel it.")
        if row.status in rules.TERMINAL_JOB:
            raise Problem(409, "invalid_state", f"The forecast job is already {row.status}.")
        previous = row.status
        # ml-forecast stops at the next step boundary and writes no prediction after it
        # (UC-15-01 alternative 3).
        row.status = "cancelled"
        row.finished_at = store.now()
        row.progress = {**(row.progress or {}), "cancelled_by": str(principal.user_id)} | (
            {"cancel_reason": body.reason.strip()} if body.reason and body.reason.strip() else {}
        )
        release.job_changed(session, row, previous)
        await session.flush()
        return ForecastJobView.model_validate(row)


@router.get(
    "/v1/forecasts/{id}/distribution",
    response_model=DistributionView,
    **endpoint("GET", "/v1/forecasts/{id}/distribution", operation_id="getForecastDistribution"),
)
async def get_distribution(
    job_id: JobId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    product_code: Annotated[str | None, Query(max_length=50)] = None,
) -> DistributionView:
    async with transaction(ROLE, principal) as session:
        row = await store.job(session, job_id, principal, data_space)
        if row.status != "ready":
            raise Problem(409, "invalid_state", f"The forecast job is {row.status}, not ready.")
        query = (
            select(fc.Prediction, fc.FormulaModel.product_code)
            .join(fc.FormulaModel, fc.FormulaModel.model_version_id == fc.Prediction.model_version_id)
            .where(fc.Prediction.job_id == row.id, fc.Prediction.scenario_id.is_(None))
            .order_by(fc.FormulaModel.product_code, fc.Prediction.period)
        )
        if product_code:
            query = query.where(fc.FormulaModel.product_code == product_code)
        predictions = (await session.execute(query)).all()
        entries = {
            (e.prediction_id): e
            for e in (
                await session.scalars(select(fc.LedgerEntry).where(fc.LedgerEntry.job_id == row.id))
            ).all()
        }
    items = []
    for prediction, code in predictions:
        entry = entries.get(prediction.id)
        contributions = prediction.contributions or {}
        items.append(
            DistributionItem(
                product_code=code,
                model_version_id=prediction.model_version_id,
                period=prediction.period,
                point=prediction.point,
                lo80=prediction.lo80,
                hi80=prediction.hi80,
                lo95=prediction.lo95,
                hi95=prediction.hi95,
                # The ledger holds the issued quantiles; an unissued run has its interval only.
                quantiles=entry.quantiles
                if entry
                else {
                    "p02_5": str(prediction.lo95),
                    "p10": str(prediction.lo80),
                    "p50": str(prediction.point),
                    "p90": str(prediction.hi80),
                    "p97_5": str(prediction.hi95),
                },
                prob_up=entry.prob_up if entry else None,
                fund_probs=entry.fund_probs if entry else contributions.get("fund_probs"),
                contributions=entry.contributions if entry else contributions,
                histogram=contributions.get("histogram"),
                baseline_value=entry.baseline_value if entry else None,
                is_experimental=prediction.is_experimental,
                ledger_entry_id=entry.id if entry else None,
            )
        )
    return DistributionView(
        job_id=row.id, cutoff_at=row.cutoff_at, items=items, fund_note=rules.FUND_NOTE
    )
