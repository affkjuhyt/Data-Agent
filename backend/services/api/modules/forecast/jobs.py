"""Forecast jobs (UC-15-01): ask for a forecast, follow it, cancel it, read its distribution.

The API never computes a forecast. A question with a still-valid forecast of a released model is
answered at once (BR-159); otherwise a forecast_job is queued with cutoff_at = now, so ml-forecast
uses only data known before the request (BR-157, point-in-time by available_at), and the 202
points at the job. ml-forecast reads forecast.job.state_changed from the outbox.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.kernel.sse import Event, event_stream, last_event_id
from services.api.modules.forecast import rules, store
from services.api.modules.forecast.schemas import (
    DistributionItem,
    DistributionView,
    ForecastAccepted,
    ForecastCancel,
    ForecastCreate,
    ForecastDetail,
    ForecastJobView,
    PredictionView,
)
from services.api.modules.forecast.store import ROLE

router = APIRouter()

JobId = Annotated[uuid.UUID, Path(alias="id")]
_NO_CANCEL = ForecastCancel()


def _job_event(
    session: AsyncSession, principal: Principal, row: fc.ForecastJob, previous: str | None
) -> None:
    store.emit(
        session,
        principal,
        "forecast_job",
        row.id,
        "forecast.job.state_changed",
        job_id=row.id,
        data_space_id=row.data_space_id,
        question_id=row.question_id,
        purpose=(row.progress or {}).get("purpose", "forecast"),
        previous_status=previous,
        status=row.status,
        model_version_id=row.model_version_id,
    )


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
    _job_event(session, principal, row, None)
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
        _job_event(session, principal, row, previous)
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
