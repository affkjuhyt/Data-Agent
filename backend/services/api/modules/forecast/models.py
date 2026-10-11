"""Forecast models and versions (UC-15-03 to UC-15-06): read, release, retrain, scenarios.

The DB guards the version life cycle and the release gate (model_version_guard: backtest of the
same version with MASE < 1, n_points, overfitting; forecast_model_current_version_check). The
routes add what the DB cannot see: who may decide (BR-158: the metric owner, never the version's
author), the state each action starts from, and the outbox events Inbox, Monitor and ml-forecast act
on. Predictions and accuracy are read point-in-time: as of a moment, only what was cut or recorded
by then (BR-157).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.forecast import jobs, rules, store
from services.api.modules.forecast.schemas import (
    AccuracyPage,
    AccuracyView,
    BacktestView,
    ForecastAccepted,
    ForecastModelDetail,
    ForecastModelView,
    Frequency,
    ModelVersionView,
    PredictionPage,
    PredictionView,
    RetrainRequest,
    ScenarioAccepted,
    ScenarioCreate,
    VersionNote,
    VersionReject,
)
from services.api.modules.forecast.store import ROLE

router = APIRouter()

ModelId = Annotated[uuid.UUID, Path(alias="id")]
VersionId = Annotated[uuid.UUID, Path(alias="id")]
_NO_NOTE = VersionNote()
_NO_RETRAIN = RetrainRequest()

Figures = dict[uuid.UUID, tuple[decimal.Decimal | None, decimal.Decimal | None]]


async def _figures(session: AsyncSession, version_ids: list[uuid.UUID]) -> Figures:
    """Per version: backtest MASE and the latest rolling MASE (A3 of GET /v1/forecast-models)."""
    if not version_ids:
        return {}
    backtests: dict[uuid.UUID, decimal.Decimal] = {
        v: m
        for v, m in (
            await session.execute(
                select(fc.ModelVersion.id, fc.BacktestRun.mase)
                .join(fc.BacktestRun, fc.BacktestRun.id == fc.ModelVersion.backtest_id)
                .where(fc.ModelVersion.id.in_(version_ids))
            )
        ).tuples()
    }
    rolling: dict[uuid.UUID, decimal.Decimal | None] = {
        v: m
        for v, m in (
            await session.execute(
                select(fc.AccuracyLog.model_version_id, fc.AccuracyLog.rolling_mase)
                .where(fc.AccuracyLog.model_version_id.in_(version_ids))
                .distinct(fc.AccuracyLog.model_version_id)
                .order_by(fc.AccuracyLog.model_version_id, fc.AccuracyLog.period.desc())
            )
        ).tuples()
    }
    return {v: (backtests.get(v), rolling.get(v)) for v in version_ids}


def _model_view(row: fc.ForecastModel, figures: Figures) -> ForecastModelView:
    view = ForecastModelView.model_validate(row)
    if row.current_version_id is not None:
        view.backtest_mase, view.rolling_mase = figures.get(row.current_version_id, (None, None))
    view.degraded = row.status == "degraded"
    return view


@router.get(
    "/v1/forecast-models",
    response_model=Page[ForecastModelView],
    **endpoint("GET", "/v1/forecast-models", operation_id="listForecastModels"),
)
async def list_models(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    metric_code: Annotated[str | None, Query(max_length=200)] = None,
    frequency: Frequency | None = None,
    status: Annotated[str | None, Query(pattern="^(active|degraded|retired)$")] = None,
) -> Page[ForecastModelView]:
    m = fc.ForecastModel
    query = select(m).where(m.org_id == principal.org_id, m.data_space_id == data_space)
    if metric_code:
        query = query.where(m.metric_code == metric_code)
    if frequency:
        query = query.where(m.frequency == frequency)
    if status:
        query = query.where(m.status == status)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(store.keyset(query, page, m.created_at, m.id))).all())
        token = store.page_token(rows, page)
        rows = rows[: page.page_size]
        figures = await _figures(session, [r.current_version_id for r in rows if r.current_version_id])
    return Page(items=[_model_view(r, figures) for r in rows], next_page_token=token)


@router.get(
    "/v1/forecast-models/{id}",
    response_model=ForecastModelDetail,
    **endpoint("GET", "/v1/forecast-models/{id}", operation_id="getForecastModel"),
)
async def get_model(
    model_id: ModelId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ForecastModelDetail:
    async with transaction(ROLE, principal) as session:
        row = await store.model(session, model_id, principal, data_space)
        figures = await _figures(session, [row.current_version_id] if row.current_version_id else [])
        out = ForecastModelDetail(**_model_view(row, figures).model_dump())
        if row.current_version_id is not None:
            current = await session.get(fc.ModelVersion, row.current_version_id)
            out.current_version = ModelVersionView.model_validate(current) if current else None
    return out


@router.get(
    "/v1/forecast-model-versions/{id}",
    response_model=ModelVersionView,
    **endpoint("GET", "/v1/forecast-model-versions/{id}", operation_id="getForecastModelVersion"),
)
async def get_version(
    version_id: VersionId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ModelVersionView:
    async with transaction(ROLE, principal) as session:
        row, _ = await store.version(session, version_id, principal, data_space)
    return ModelVersionView.model_validate(row)


async def _backtest(session: AsyncSession, row: fc.ModelVersion) -> fc.BacktestRun | None:
    if row.backtest_id is not None:
        return await session.get(fc.BacktestRun, row.backtest_id)
    # Before the version points at one, its latest run is the one under way.
    found: fc.BacktestRun | None = await session.scalar(
        select(fc.BacktestRun)
        .where(fc.BacktestRun.model_version_id == row.id)
        .order_by(fc.BacktestRun.started_at.desc())
        .limit(1)
    )
    return found


@router.get(
    "/v1/forecast-model-versions/{id}/backtest",
    response_model=BacktestView,
    **endpoint(
        "GET", "/v1/forecast-model-versions/{id}/backtest", operation_id="getForecastBacktest"
    ),
)
async def get_backtest(
    version_id: VersionId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> BacktestView:
    async with transaction(ROLE, principal) as session:
        row, _ = await store.version(session, version_id, principal, data_space)
        run = await _backtest(session, row)
    if run is None:
        raise Problem(404, "not_found", "The version has no backtest yet.")
    return BacktestView.model_validate(run)


@router.post(
    "/v1/forecast-model-versions/{id}:submit",
    response_model=ModelVersionView,
    **endpoint(
        "POST", "/v1/forecast-model-versions/{id}:submit", operation_id="submitForecastModelVersion"
    ),
)
async def submit_version(
    version_id: VersionId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[VersionNote, Body()] = _NO_NOTE,
) -> ModelVersionView:
    async with transaction(ROLE, principal) as session:
        row, owner = await store.version(session, version_id, principal, data_space, lock=True)
        if row.status != "draft":
            raise Problem(409, "invalid_state", f"The version is {row.status}, not draft.")
        if row.algorithm == "seasonal_naive":
            # UC-15-03 E1: the baseline is answered with its label, never sent for release.
            raise Problem(
                422,
                "business_rule_violated",
                "The seasonal naive baseline is not submitted for release (BR-158).",
            )
        run = await _backtest(session, row)
        if run is None or run.finished_at is None or run.mase >= 1:
            mase = "none" if run is None else str(run.mase)
            raise Problem(
                422,
                "business_rule_violated",
                f"Release needs a finished backtest beating seasonal naive, MASE < 1; MASE is {mase} (BR-158).",
            )
        row.backtest_id = run.id
        row.status = "pending_approval"
        row.updated_by, row.updated_at = principal.user_id, store.now()
        store.review_note(row, principal, "submit", body.note)
        # Inbox opens the forecast_model HITL request for the metric owner (UC-15-04 trigger).
        store.emit(
            session,
            principal,
            "model_version",
            row.id,
            "forecast.model_version.submitted",
            model_version_id=row.id,
            model_id=owner.id,
            data_space_id=owner.data_space_id,
            hitl_kind="forecast_model",
            assignee_id=owner.owner_id,
            backtest_id=run.id,
            status=row.status,
        )
        await session.flush()
        return ModelVersionView.model_validate(row)


def _check_decider(row: fc.ModelVersion, owner: fc.ForecastModel, principal: Principal) -> None:
    """BR-158 / UC-15-04 E1: the metric owner decides, never whoever built the version."""
    if row.created_by == principal.user_id:
        raise Problem(
            403, "policy_denied", "Whoever built the version cannot decide on it (BR-158)."
        )
    if owner.owner_id != principal.user_id:
        raise Problem(
            403, "policy_denied", "Only the metric owner decides on a forecast model (BR-158)."
        )


@router.post(
    "/v1/forecast-model-versions/{id}:approve",
    response_model=ModelVersionView,
    **endpoint(
        "POST",
        "/v1/forecast-model-versions/{id}:approve",
        operation_id="approveForecastModelVersion",
    ),
)
async def approve_version(
    version_id: VersionId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[VersionNote, Body()] = _NO_NOTE,
) -> ModelVersionView:
    async with transaction(ROLE, principal) as session:
        row, owner = await store.version(session, version_id, principal, data_space, lock=True)
        if row.status != "pending_approval":
            raise Problem(409, "invalid_state", f"The version is {row.status}, not pending_approval.")
        _check_decider(row, owner, principal)
        now = store.now()
        previous_id = owner.current_version_id
        if previous_id is not None and previous_id != row.id:
            old = await session.get(fc.ModelVersion, previous_id, with_for_update=True)
            if old is not None and old.status in rules.SERVING_VERSION:
                old.status = "retired"
                old.updated_by, old.updated_at = principal.user_id, now
                store.emit(
                    session,
                    principal,
                    "model_version",
                    old.id,
                    "forecast.model_version.retired",
                    model_version_id=old.id,
                    model_id=owner.id,
                    data_space_id=owner.data_space_id,
                    status=old.status,
                )
                # One published version per model (partial unique index): retire first.
                await session.flush()
        row.status = "published"
        row.approved_by, row.approved_at = principal.user_id, now
        row.updated_by, row.updated_at = principal.user_id, now
        store.review_note(row, principal, "approve", body.note)
        previous_model_status = owner.status
        owner.current_version_id = row.id
        # A newly released version serves an active model, whatever its predecessor was (BR-160).
        owner.status = "active"
        owner.updated_by, owner.updated_at = principal.user_id, now
        # ml-forecast recomputes the model's forecasts and monitors on this event (UC-15-04 step 4).
        store.emit(
            session,
            principal,
            "model_version",
            row.id,
            "forecast.model.published",
            model_version_id=row.id,
            model_id=owner.id,
            data_space_id=owner.data_space_id,
            previous_version_id=previous_id,
            status=row.status,
            approved_by=principal.user_id,
        )
        if previous_model_status != owner.status:
            store.emit(
                session,
                principal,
                "forecast_model",
                owner.id,
                "forecast.model.status_changed",
                model_id=owner.id,
                data_space_id=owner.data_space_id,
                previous_status=previous_model_status,
                status=owner.status,
            )
        await session.flush()
        return ModelVersionView.model_validate(row)


@router.post(
    "/v1/forecast-model-versions/{id}:reject",
    response_model=ModelVersionView,
    **endpoint(
        "POST", "/v1/forecast-model-versions/{id}:reject", operation_id="rejectForecastModelVersion"
    ),
)
async def reject_version(
    body: VersionReject,
    version_id: VersionId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ModelVersionView:
    async with transaction(ROLE, principal) as session:
        row, owner = await store.version(session, version_id, principal, data_space, lock=True)
        if row.status != "pending_approval":
            raise Problem(409, "invalid_state", f"The version is {row.status}, not pending_approval.")
        # Approve and reject are one decision (UC-15-04 step 3) under one set of rules.
        _check_decider(row, owner, principal)
        row.status = "rejected"
        row.updated_by, row.updated_at = principal.user_id, store.now()
        # AC-15-04-02: the reason stays with the decision.
        store.review_note(row, principal, "reject", body.reason)
        store.emit(
            session,
            principal,
            "model_version",
            row.id,
            "forecast.model_version.rejected",
            model_version_id=row.id,
            model_id=owner.id,
            data_space_id=owner.data_space_id,
            status=row.status,
            rejected_by=principal.user_id,
        )
        await session.flush()
        return ModelVersionView.model_validate(row)


@router.post(
    "/v1/forecast-models/{id}:retrain",
    status_code=202,
    response_model=ForecastAccepted,
    **endpoint("POST", "/v1/forecast-models/{id}:retrain", operation_id="retrainForecastModel"),
)
async def retrain_model(
    model_id: ModelId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[RetrainRequest, Body()] = _NO_RETRAIN,
) -> ForecastAccepted:
    async with transaction(ROLE, principal) as session:
        # Locked: two clicks on "Huấn luyện lại" must not both find no run going.
        owner = await store.model(session, model_id, principal, data_space, lock=True)
        if owner.status == "retired":
            raise Problem(409, "invalid_state", "The model is retired.")
        running = await session.scalar(
            select(fc.ForecastJob.id).where(
                fc.ForecastJob.org_id == principal.org_id,
                fc.ForecastJob.data_space_id == data_space,
                fc.ForecastJob.status.not_in(rules.TERMINAL_JOB),
                fc.ForecastJob.progress["purpose"].astext == "retrain",
                fc.ForecastJob.progress["model_id"].astext == str(owner.id),
            )
        )
        if running is not None:
            raise Problem(409, "invalid_state", "A retraining of this model is already running.")
        if body.training_to is not None and body.training_to > store.now().date():
            raise Problem(
                422,
                "validation_failed",
                "training_to cannot be in the future (BR-157).",
                errors=[FieldError(field="training_to", code="future", message="In the future.")],
            )
        horizon = rules.DEFAULT_HORIZON[owner.frequency]
        if owner.current_version_id is not None:
            current = await session.get(fc.ModelVersion, owner.current_version_id)
            run = await _backtest(session, current) if current else None
            horizon = run.horizon if run else horizon
        progress: dict[str, object] = {"purpose": "retrain", "model_id": str(owner.id)}
        if body.training_to is not None:
            progress["training_to"] = body.training_to.isoformat()
        if body.reason and body.reason.strip():
            progress["reason"] = body.reason.strip()
        row = jobs.new_job(
            session,
            principal,
            data_space,
            metric_code=owner.metric_code,
            frequency=owner.frequency,
            horizon=horizon,
            scope=owner.scope,
            progress=progress,
        )
        await session.flush()
        return jobs.accepted(response, row)


async def _serving_version(
    session: AsyncSession, owner: fc.ForecastModel, version_id: uuid.UUID | None = None
) -> fc.ModelVersion | None:
    """The asked version of this model, else the released one, else the newest one."""
    v = fc.ModelVersion
    if version_id is not None:
        found: fc.ModelVersion | None = await session.scalar(
            select(v).where(v.id == version_id, v.model_id == owner.id)
        )
        if found is None:
            raise Problem(
                422,
                "validation_failed",
                "model_version_id is not a version of this model.",
                errors=[FieldError(field="model_version_id", code="not_found", message="No such version.")],
            )
        return found
    if owner.current_version_id is not None:
        return await session.get(v, owner.current_version_id)
    newest: fc.ModelVersion | None = await session.scalar(
        select(v)
        .where(v.model_id == owner.id, v.status != "rejected")
        .order_by(v.version.desc())
        .limit(1)
    )
    return newest


@router.post(
    "/v1/forecast-models/{id}/scenarios",
    status_code=202,
    response_model=ScenarioAccepted,
    **endpoint("POST", "/v1/forecast-models/{id}/scenarios", operation_id="createForecastScenario"),
)
async def create_scenario(
    body: ScenarioCreate,
    model_id: ModelId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ScenarioAccepted:
    assumptions = [a.model_dump(mode="json") for a in body.assumptions]
    async with transaction(ROLE, principal) as session:
        owner = await store.model(session, model_id, principal, data_space)
        serving = await _serving_version(session, owner)
        if serving is None:
            raise Problem(409, "invalid_state", "The model has no version to run a scenario on.")
        unknown = {a["feature"] for a in assumptions} - rules.feature_names(serving.features)
        if unknown:
            # UC-15-05 E1: a scenario on a variable the model does not use does not apply.
            raise Problem(
                422,
                "business_rule_violated",
                f"The model does not use {', '.join(sorted(unknown))}; the scenario does not apply.",
            )
        row = fc.Scenario(
            id=uuid7(),
            org_id=principal.org_id,
            model_id=owner.id,
            name=body.name,
            assumptions=assumptions,
            created_by=principal.user_id,
        )
        session.add(row)
        # ml-forecast reruns the trained version with the fixed inputs (no new backtest,
        # AC-15-05-02) and writes the scenario's predictions as scenario_fixed (BR-173).
        store.emit(
            session,
            principal,
            "scenario",
            row.id,
            "forecast.scenario.created",
            scenario_id=row.id,
            model_id=owner.id,
            model_version_id=serving.id,
            data_space_id=owner.data_space_id,
        )
        await session.flush()
    response.headers["Location"] = (
        f"/v1/forecast-models/{owner.id}/predictions?scenario_id={row.id}"
    )
    return ScenarioAccepted(
        id=row.id,
        status="queued",
        poll_after_ms=rules.POLL_AFTER_MS,
        model_id=owner.id,
        model_version_id=serving.id,
        name=row.name,
        assumptions=assumptions,
        notice=rules.scenario_notice(assumptions, owner.metric_code),
    )


@router.get(
    "/v1/forecast-models/{id}/predictions",
    response_model=PredictionPage,
    **endpoint(
        "GET", "/v1/forecast-models/{id}/predictions", operation_id="listForecastPredictions"
    ),
)
async def list_predictions(
    model_id: ModelId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    scenario_id: uuid.UUID | None = None,
    model_version_id: uuid.UUID | None = None,
    period_from: Annotated[datetime.date | None, Query(alias="from")] = None,
    period_to: Annotated[datetime.date | None, Query(alias="to")] = None,
    as_of: Annotated[
        datetime.datetime | None,
        Query(description="Only forecasts cut by this moment, the latest per period (BR-157)"),
    ] = None,
) -> PredictionPage:
    p = fc.Prediction
    async with transaction(ROLE, principal) as session:
        owner = await store.model(session, model_id, principal, data_space)
        serving = await _serving_version(session, owner, model_version_id)
        if serving is None:
            return PredictionPage(items=[])
        scenario = None
        if scenario_id is not None:
            scenario = await session.scalar(
                select(fc.Scenario).where(
                    fc.Scenario.id == scenario_id, fc.Scenario.model_id == owner.id
                )
            )
            if scenario is None:
                raise Problem(404, "not_found", "Scenario not found.")
        # Point in time: for each period, the latest forecast cut by as_of.
        query = (
            select(p)
            .where(p.model_version_id == serving.id, p.cutoff_at <= (as_of or store.now()))
            .where(p.scenario_id == scenario_id if scenario_id else p.scenario_id.is_(None))
            .distinct(p.period)
            .order_by(p.period, p.cutoff_at.desc())
        )
        if period_from:
            query = query.where(p.period >= period_from)
        if period_to:
            query = query.where(p.period <= period_to)
        if page.cursor:
            try:
                after = datetime.date.fromisoformat(page.cursor["period"])
            except (KeyError, TypeError, ValueError) as exc:
                raise invalid_page_token() from exc
            query = query.where(p.period > after)
        rows = list((await session.scalars(query.limit(page.page_size + 1))).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = encode_page_token({"period": rows[-1].period.isoformat()} if more else None)
    assumptions = scenario.assumptions if scenario else serving.features
    return PredictionPage(
        items=[PredictionView.model_validate(r) for r in rows],
        next_page_token=token,
        model_version_id=serving.id,
        labels=rules.labels(
            serving.status, serving.algorithm, any(r.is_experimental for r in rows)
        ),
        assumptions=assumptions,
        notice=rules.scenario_notice(scenario.assumptions, owner.metric_code) if scenario else None,
    )


@router.get(
    "/v1/forecast-models/{id}/accuracy",
    response_model=AccuracyPage,
    **endpoint("GET", "/v1/forecast-models/{id}/accuracy", operation_id="listForecastAccuracy"),
)
async def list_accuracy(
    model_id: ModelId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    period_from: Annotated[datetime.date | None, Query(alias="from")] = None,
    period_to: Annotated[datetime.date | None, Query(alias="to")] = None,
    as_of: Annotated[
        datetime.datetime | None, Query(description="Only errors recorded by this moment")
    ] = None,
) -> AccuracyPage:
    a = fc.AccuracyLog
    async with transaction(ROLE, principal) as session:
        owner = await store.model(session, model_id, principal, data_space)
        versions = select(fc.ModelVersion.id).where(fc.ModelVersion.model_id == owner.id)
        query = select(a).where(a.model_version_id.in_(versions))
        if as_of:
            query = query.where(a.recorded_at <= as_of)
        if period_from:
            query = query.where(a.period >= period_from)
        if period_to:
            query = query.where(a.period <= period_to)
        if page.cursor:
            try:
                cursor = (
                    datetime.date.fromisoformat(page.cursor["period"]),
                    uuid.UUID(page.cursor["id"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise invalid_page_token() from exc
            query = query.where(tuple_(a.period, a.id) < cursor)
        rows = list(
            (
                await session.scalars(
                    query.order_by(a.period.desc(), a.id.desc()).limit(page.page_size + 1)
                )
            ).all()
        )
        figures = await _figures(
            session, [owner.current_version_id] if owner.current_version_id else []
        )
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    backtest_mase, rolling_mase = (
        figures.get(owner.current_version_id, (None, None))
        if owner.current_version_id
        else (None, None)
    )
    return AccuracyPage(
        items=[AccuracyView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(
            {"period": last.period.isoformat(), "id": str(last.id)} if last else None
        ),
        rolling_mase=rolling_mase,
        backtest_mase=backtest_mase,
        in80_rate=rules.rate([r.in80 for r in rows]),
        in95_rate=rules.rate([r.in95 for r in rows]),
        degraded=owner.status == "degraded",
    )
