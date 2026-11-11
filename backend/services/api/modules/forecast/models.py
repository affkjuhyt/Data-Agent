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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from libs import forecast_release as release
from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, governance
from libs.dataagent_db.models import forecast as fc
from libs.dataagent_db.models import org as org_models
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.audit import record_self_approval
from services.api.modules.forecast import jobs, rules, store
from services.api.modules.forecast.schemas import (
    AccuracyPage,
    AccuracyView,
    BacktestView,
    ForecastAccepted,
    ForecastModelCreate,
    ForecastModelCreated,
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
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "updated_at", "metric_code", "status", default="created_at desc")
        ),
    ],
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
    query = keyset(
        query,
        columns={
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "metric_code": m.metric_code,
            "status": m.status,
        },
        tiebreak=m.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        figures = await _figures(
            session, [r.current_version_id for r in rows if r.current_version_id]
        )
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
        figures = await _figures(
            session, [row.current_version_id] if row.current_version_id else []
        )
        out = ForecastModelDetail(**_model_view(row, figures).model_dump())
        if row.current_version_id is not None:
            current = await session.get(fc.ModelVersion, row.current_version_id)
            out.current_version = ModelVersionView.model_validate(current) if current else None
        pending = await session.scalar(
            select(fc.ModelVersion)
            .where(fc.ModelVersion.model_id == row.id, fc.ModelVersion.status == "pending_approval")
            .order_by(fc.ModelVersion.version.desc())
            .limit(1)
        )
        out.pending_version = ModelVersionView.model_validate(pending) if pending else None
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
        run = await release.latest_backtest(session, row)
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
        try:
            await release.submit(session, row, owner, by=principal.user_id, note=body.note)
        except release.SubmitRefused as exc:
            raise Problem(exc.status, exc.code, str(exc)) from exc
        await session.flush()
        return ModelVersionView.model_validate(row)


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
        try:
            self_approved = await release.approve(
                session,
                row,
                owner,
                by=principal.user_id,
                note=body.note,
                allow_self=principal.self_approval,
            )
        except release.ApproveRefused as exc:
            raise Problem(exc.status, exc.code, str(exc)) from exc
        if self_approved:
            await record_self_approval(
                principal,
                step="forecast_model.decide",
                object_kind="model_version",
                object_id=row.id,
            )
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
        try:
            self_decided = await release.reject(
                session,
                row,
                owner,
                by=principal.user_id,
                reason=body.reason,
                allow_self=principal.self_approval,
            )
        except release.ApproveRefused as exc:
            raise Problem(exc.status, exc.code, str(exc)) from exc
        if self_decided:
            await record_self_approval(
                principal,
                step="forecast_model.decide",
                object_kind="model_version",
                object_id=row.id,
            )
        return ModelVersionView.model_validate(row)


def _in_force(version: context.MetricVersion, at: datetime.datetime) -> bool:
    """The current definition is certified and its validity window covers `at`."""
    return (
        version.status == "certified"
        and version.certified_at is not None
        and version.certified_at <= at
        and (version.valid_from is None or version.valid_from <= at)
        and (version.valid_to is None or version.valid_to > at)
    )


def _policy_applies(
    policy: governance.AccessPolicy,
    principal: Principal,
    role_ids: set[uuid.UUID],
    group_ids: set[uuid.UUID],
) -> bool:
    """An empty applies_to covers everyone; otherwise the caller must be named."""
    raw = policy.applies_to if isinstance(policy.applies_to, dict) else {}
    users = {str(value) for value in raw.get("user_ids") or []}
    roles = {str(value) for value in raw.get("role_ids") or []}
    groups = {str(value) for value in raw.get("group_ids") or []}
    # Defining a model is the person themselves, not an agent run.
    actors = {str(value) for value in raw.get("actors") or []}
    if actors and "person" not in actors:
        return False
    if not (users or roles or groups):
        return True
    return (
        str(principal.user_id) in users
        or bool(roles & {str(role) for role in role_ids})
        or bool(groups & {str(group) for group in group_ids})
    )


def _column_names(expression: str) -> set[str] | None:
    """Column names in `expression`, or None when it cannot be parsed (fail closed)."""
    try:
        return {column.name for column in parse_one(expression).find_all(exp.Column)}
    except ParseError:
        return None


async def _policy_blocks(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    version: context.MetricVersion,
) -> bool:
    """A column or mask policy that applies to the caller covers a measure this metric reads.

    The same rule as the semantic layer's blocked_metric_ids, limited to policies that apply
    to this caller: a hidden table, or a masked column of a measure, means the caller may not
    define a forecast on the metric.
    """
    names = _column_names(version.expression)
    if names is None:
        # Fail closed, but not as a permission denial — the formula itself cannot be checked.
        raise Problem(
            422,
            "business_rule_violated",
            "The metric formula cannot be checked.",
        )
    if not names:
        return False
    measures = (
        await session.scalars(
            select(context.SemanticMeasure).where(
                context.SemanticMeasure.data_space_id == data_space,
                context.SemanticMeasure.code.in_(names),
            )
        )
    ).all()
    if not measures:
        return False
    entities = {
        row.id: row
        for row in (
            await session.scalars(
                select(context.SemanticEntity).where(
                    context.SemanticEntity.id.in_({measure.entity_id for measure in measures})
                )
            )
        ).all()
    }
    dataset_ids = {
        entity.primary_dataset_id
        for measure in measures
        if (entity := entities.get(measure.entity_id)) is not None
    }
    if not dataset_ids:
        return False
    policies = (
        await session.scalars(
            select(governance.AccessPolicy).where(
                governance.AccessPolicy.data_space_id == data_space,
                governance.AccessPolicy.status == "active",
                governance.AccessPolicy.kind.in_(("column", "mask")),
                governance.AccessPolicy.dataset_id.in_(dataset_ids),
            )
        )
    ).all()
    if not policies:
        return False
    role_ids = set(
        (
            await session.scalars(
                select(org_models.UserRole.role_id).where(
                    org_models.UserRole.user_id == principal.user_id
                )
            )
        ).all()
    )
    group_ids = set(
        (
            await session.scalars(
                select(org_models.UserGroupMember.group_id).where(
                    org_models.UserGroupMember.user_id == principal.user_id
                )
            )
        ).all()
    )
    for measure in measures:
        entity = entities.get(measure.entity_id)
        if entity is None:
            continue
        physical = _column_names(measure.expression)
        for policy in policies:
            if policy.dataset_id != entity.primary_dataset_id:
                continue
            if not _policy_applies(policy, principal, role_ids, group_ids):
                continue
            # A column policy hides the table the measure is read from.
            if policy.kind == "column":
                return True
            masked = {column.lower() for column in policy.columns or []}
            if physical is None or any(name.lower() in masked for name in physical):
                return True
    return False


async def _usable_metric(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, code: str
) -> tuple[context.Metric, context.MetricVersion]:
    """The metric the caller may forecast: active, current version certified and in force,
    and not blocked by a column or mask policy that applies to the caller."""
    metric = await session.scalar(
        select(context.Metric).where(
            context.Metric.org_id == principal.org_id,
            context.Metric.data_space_id == data_space,
            context.Metric.code == code,
            context.Metric.status == "active",
        )
    )
    if metric is None:
        raise Problem(404, "not_found", "Metric not found.")
    version = None
    if metric.current_version is not None:
        version = await session.scalar(
            select(context.MetricVersion).where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.version == metric.current_version,
            )
        )
    if (
        version is None
        or not _in_force(version, store.now())
        or await _policy_blocks(session, principal, data_space, version)
    ):
        raise Problem(403, "policy_denied", "You may not use this metric.")
    return metric, version


def _scope_refused(codes: list[str]) -> Problem:
    named = ", ".join(codes)
    return Problem(
        422,
        "business_rule_violated",
        f"The metric does not allow the cut {named}.",
        errors=[
            FieldError(
                field=f"scope.{code}",
                code="not_allowed",
                message=f"{code} is not a cut of this metric.",
            )
            for code in codes
        ],
    )


async def _check_scope(
    session: AsyncSession,
    data_space: uuid.UUID,
    version: context.MetricVersion,
    scope: dict[str, list[str]],
) -> None:
    """Every scope key is a dimension the metric's current version allows."""
    if not scope:
        return
    found = (
        await session.scalars(
            select(context.SemanticDimension).where(
                context.SemanticDimension.data_space_id == data_space,
                context.SemanticDimension.code.in_(list(scope)),
            )
        )
    ).all()
    by_code = {row.code: row.id for row in found}
    allowed = set(version.allowed_dimensions or [])
    refused = [code for code in scope if by_code.get(code) not in allowed]
    if refused:
        raise _scope_refused(refused)


@router.post(
    "/v1/forecast-models",
    status_code=201,
    response_model=ForecastModelCreated,
    **endpoint("POST", "/v1/forecast-models", operation_id="createForecastModel"),
)
async def create_model(
    body: ForecastModelCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ForecastModelCreated:
    # Column and mask policies live in governance, which svc_forecast cannot read. svc_qa can,
    # and it is the role that already decides whether a caller may use a metric.
    async with transaction("svc_qa", principal) as reading:
        metric, version = await _usable_metric(reading, principal, data_space, body.metric_code)
        await _check_scope(reading, data_space, version, body.scope)
        owner_id = metric.owner_id
        metric_code = metric.code
    async with transaction(ROLE, principal) as session:
        existing = await session.scalar(
            select(fc.ForecastModel.id).where(
                fc.ForecastModel.org_id == principal.org_id,
                fc.ForecastModel.data_space_id == data_space,
                fc.ForecastModel.metric_code == metric_code,
                fc.ForecastModel.frequency == body.frequency,
                fc.ForecastModel.scope == body.scope,
            )
        )
        if existing is not None:
            raise Problem(409, "conflict", "A forecast model already covers this series.")
        row = fc.ForecastModel(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            metric_code=metric_code,
            scope=body.scope,
            frequency=body.frequency,
            owner_id=owner_id,
            status="active",
            created_by=principal.user_id,
        )
        session.add(row)
        job = jobs.new_job(
            session,
            principal,
            data_space,
            metric_code=metric_code,
            frequency=body.frequency,
            horizon=body.horizon,
            scope=body.scope,
            progress={"purpose": "forecast", "model_id": str(row.id)},
        )
        await session.flush()
        await session.refresh(row)
    response.headers["Location"] = f"/v1/forecast-models/{row.id}"
    return ForecastModelCreated(**_model_view(row, {}).model_dump(), job_id=job.id)


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
            run = await release.latest_backtest(session, current) if current else None
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
                errors=[
                    FieldError(
                        field="model_version_id", code="not_found", message="No such version."
                    )
                ],
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
    order: Annotated[Order, Depends(order_by("period", "recorded_at", default="period desc"))],
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
        query = keyset(
            query,
            columns={"period": a.period, "recorded_at": a.recorded_at},
            tiebreak=a.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        figures = await _figures(
            session, [owner.current_version_id] if owner.current_version_id else []
        )
    backtest_mase, rolling_mase = (
        figures.get(owner.current_version_id, (None, None))
        if owner.current_version_id
        else (None, None)
    )
    return AccuracyPage(
        items=[AccuracyView.model_validate(r) for r in rows],
        next_page_token=token,
        rolling_mase=rolling_mase,
        backtest_mase=backtest_mase,
        in80_rate=rules.rate([r.in80 for r in rows]),
        in95_rate=rules.rate([r.in95 for r in rows]),
        degraded=owner.status == "degraded",
    )
