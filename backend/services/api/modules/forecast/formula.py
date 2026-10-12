"""Formula forecasts (UC-15-07): formula models, their legal parameters, Monte Carlo runs.

A formula model is compiled from a certified metric version (context.metric_version, readable by
svc_forecast) and cites only parameters of that expression (BR-183). Parameters come from legal
documents and are certified by a second person (BR-182, formula_component_guard). A run checks the
gate the API can see — certified formula, a certified value of every parameter in force at the
period and known by now — then queues a forecast_job for ml-forecast (202). Parameter values are
always read point-in-time: in force at the moment asked and certified by then.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context
from libs.dataagent_db.models import forecast as fc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.forecast import jobs, rules, store
from services.api.modules.forecast.schemas import (
    ComponentCertify,
    ComponentCreate,
    ComponentView,
    ForecastAccepted,
    FormulaModelCreate,
    FormulaModelView,
    FormulaRunCreate,
)
from services.api.modules.forecast.store import ROLE

router = APIRouter()

FormulaId = Annotated[uuid.UUID, Path(alias="id")]
ComponentId = Annotated[uuid.UUID, Path(alias="id")]
_NO_CERTIFY = ComponentCertify()

# A certified value stays the value of its period after a successor supersedes it.
IN_FORCE = ("certified", "superseded")


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )


def _view(
    row: fc.FormulaModel, version: fc.ModelVersion, reconstruction_mae: Any = None
) -> FormulaModelView:
    return FormulaModelView(
        id=row.id,
        model_id=version.model_id,
        model_version_id=row.model_version_id,
        version_status=version.status,
        metric_version_id=row.metric_version_id,
        product_code=row.product_code,
        formula=row.formula,
        window_rule=row.window_rule,
        input_series=row.input_series,
        nowcast_method=row.nowcast_method,
        fund_model=row.fund_model,
        n_draws=row.n_draws,
        miss_abs_threshold=row.miss_abs_threshold,
        rounding=row.rounding,
        owner_id=row.owner_id,
        reconstruction_mae=reconstruction_mae,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _reconstruction(session: AsyncSession, version: fc.ModelVersion) -> Any:
    if version.backtest_id is None:
        return None
    run = await session.get(fc.BacktestRun, version.backtest_id)
    return (run.metrics or {}).get("reconstruction_mae") if run else None


@router.get(
    "/v1/formula-models",
    response_model=Page[FormulaModelView],
    **endpoint("GET", "/v1/formula-models", operation_id="listFormulaModels"),
)
async def list_formula_models(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    metric_code: Annotated[str | None, Query(max_length=200)] = None,
    product_code: Annotated[str | None, Query(max_length=50)] = None,
    status: Annotated[
        str | None,
        Query(pattern="^(draft|pending_approval|published|degraded|retired|rejected)$"),
    ] = None,
) -> Page[FormulaModelView]:
    f = fc.FormulaModel
    query = (
        select(f, fc.ModelVersion, fc.BacktestRun.metrics)
        .join(fc.ModelVersion, fc.ModelVersion.id == f.model_version_id)
        .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
        .outerjoin(fc.BacktestRun, fc.BacktestRun.id == fc.ModelVersion.backtest_id)
        .where(f.org_id == principal.org_id, fc.ForecastModel.data_space_id == data_space)
    )
    if metric_code:
        query = query.where(fc.ForecastModel.metric_code == metric_code)
    if product_code:
        query = query.where(f.product_code == product_code)
    if status:
        query = query.where(fc.ModelVersion.status == status)
    async with transaction(ROLE, principal) as session:
        rows = (await session.execute(store.keyset(query, page, f.created_at, f.id))).all()
    models = [r[0] for r in rows]
    token = store.page_token(models, page)
    return Page(
        items=[
            _view(row, version, (metrics or {}).get("reconstruction_mae"))
            for row, version, metrics in rows[: page.page_size]
        ],
        next_page_token=token,
    )


async def _certified_metric_version(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    metric_version_id: uuid.UUID,
) -> tuple[context.MetricVersion, context.Metric] | None:
    found = (
        await session.execute(
            select(context.MetricVersion, context.Metric)
            .join(context.Metric, context.Metric.id == context.MetricVersion.metric_id)
            .where(
                context.MetricVersion.id == metric_version_id,
                context.Metric.org_id == principal.org_id,
                context.Metric.data_space_id == data_space,
            )
        )
    ).first()
    return (found[0], found[1]) if found else None


def _metric_version_in_force(mv: context.MetricVersion, at: datetime.datetime) -> bool:
    return (
        mv.status == "certified"
        and mv.certified_at is not None
        and mv.certified_at <= at
        and (mv.valid_to is None or mv.valid_to > at)
    )


@router.post(
    "/v1/formula-models",
    status_code=201,
    response_model=FormulaModelView,
    **endpoint("POST", "/v1/formula-models", operation_id="createFormulaModel"),
)
async def create_formula_model(
    body: FormulaModelCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> FormulaModelView:
    parameters = rules.formula_parameters(body.formula)
    async with transaction(ROLE, principal) as session:
        # Locked: the next version number of the model is taken here.
        owner = await session.scalar(
            select(fc.ForecastModel)
            .where(
                fc.ForecastModel.id == body.model_id,
                fc.ForecastModel.org_id == principal.org_id,
                fc.ForecastModel.data_space_id == data_space,
            )
            .with_for_update()
        )
        if owner is None:
            raise _invalid("model_id", "model_id is not a forecast model of this Data Space.")
        found = await _certified_metric_version(
            session, principal, data_space, body.metric_version_id
        )
        if found is None:
            raise _invalid(
                "metric_version_id", "metric_version_id is not a metric version of this Data Space."
            )
        metric_version, metric = found
        if metric.code != owner.metric_code:
            raise _invalid(
                "metric_version_id",
                f"The metric version is of {metric.code}, the model forecasts {owner.metric_code}.",
            )
        now = store.now()
        if not _metric_version_in_force(metric_version, now):
            raise Problem(
                422,
                "business_rule_violated",
                f"The metric version is {metric_version.status}; a formula model needs a certified one (BR-183).",
            )
        missing = parameters - rules.expression_names(metric_version.expression)
        if missing:
            raise Problem(
                422,
                "business_rule_violated",
                "The formula cites parameters the certified expression does not have: "
                f"{', '.join(sorted(missing))} (BR-183).",
            )
        last = await session.scalar(
            select(func.max(fc.ModelVersion.version)).where(fc.ModelVersion.model_id == owner.id)
        )
        today = now.date()
        # A formula is not fitted on a series: the training window is a placeholder that
        # ml-forecast overwrites when it fits the Fund model on the published periods.
        valid_from = metric_version.valid_from.date() if metric_version.valid_from else None
        start = (
            valid_from if valid_from and valid_from < today else today - datetime.timedelta(days=1)
        )
        version = fc.ModelVersion(
            id=uuid7(),
            org_id=principal.org_id,
            model_id=owner.id,
            version=(last or 0) + 1,
            algorithm="formula_mc",
            training_from=start,
            training_to=today,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(version)
        await session.flush()
        row = fc.FormulaModel(
            id=uuid7(),
            org_id=principal.org_id,
            model_version_id=version.id,
            metric_version_id=metric_version.id,
            product_code=body.product_code,
            formula=body.formula,
            window_rule=body.window_rule,
            input_series=body.input_series,
            nowcast_method=body.nowcast_method,
            n_draws=body.n_draws,
            miss_abs_threshold=body.miss_abs_threshold,
            rounding=body.rounding,
            owner_id=owner.owner_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        store.emit(
            session,
            principal,
            "model_version",
            version.id,
            "forecast.model_version.created",
            model_version_id=version.id,
            model_id=owner.id,
            data_space_id=data_space,
            algorithm=version.algorithm,
            status=version.status,
        )
        store.emit(
            session,
            principal,
            "formula_model",
            row.id,
            "forecast.formula_model.created",
            formula_model_id=row.id,
            model_version_id=version.id,
            model_id=owner.id,
            metric_version_id=metric_version.id,
            data_space_id=data_space,
            product_code=row.product_code,
        )
        await session.flush()
        await session.refresh(row)
        await session.refresh(version)
        response.headers["Location"] = f"/v1/formula-models/{row.id}"
        return _view(row, version)


@router.get(
    "/v1/formula-models/{id}",
    response_model=FormulaModelView,
    **endpoint("GET", "/v1/formula-models/{id}", operation_id="getFormulaModel"),
)
async def get_formula_model(
    formula_id: FormulaId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> FormulaModelView:
    async with transaction(ROLE, principal) as session:
        row, version, _ = await store.formula_model(session, formula_id, principal, data_space)
        return _view(row, version, await _reconstruction(session, version))


def _products(product_code: str) -> tuple[str, ...]:
    # '*' parameters (vat_rate) apply to every product.
    return (product_code, "*")


def _component_view(row: fc.FormulaComponent) -> ComponentView:
    return ComponentView(
        **{k: getattr(row, k) for k in ComponentView.model_fields if k != "entered_by"},
        entered_by=row.created_by,
    )


@router.get(
    "/v1/formula-models/{id}/components",
    response_model=Page[ComponentView],
    **endpoint("GET", "/v1/formula-models/{id}/components", operation_id="listFormulaComponents"),
)
async def list_components(
    formula_id: FormulaId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "code",
                "valid_from",
                "status",
                default="created_at desc",
            )
        ),
    ],
    at: Annotated[
        datetime.datetime | None,
        Query(description="The values in force at this moment and certified by then"),
    ] = None,
    status: Annotated[
        str | None, Query(pattern="^(draft|pending_review|certified|superseded|rejected)$")
    ] = None,
) -> Page[ComponentView]:
    c = fc.FormulaComponent
    async with transaction(ROLE, principal) as session:
        row, _, _ = await store.formula_model(session, formula_id, principal, data_space)
        query = select(c).where(
            c.org_id == principal.org_id,
            c.code.in_(sorted(rules.formula_parameters(row.formula))),
            c.product_code.in_(_products(row.product_code)),
        )
        if at is not None:
            query = query.where(
                c.status.in_(IN_FORCE),
                c.valid_from <= at,
                or_(c.valid_to.is_(None), c.valid_to > at),
                c.certified_at <= at,
            )
        if status:
            query = query.where(c.status == status)
        query = keyset(
            query,
            columns={
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "code": c.code,
                "valid_from": c.valid_from,
                "status": c.status,
            },
            tiebreak=c.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[_component_view(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/formula-models/{id}/components",
    status_code=201,
    response_model=ComponentView,
    **endpoint("POST", "/v1/formula-models/{id}/components", operation_id="createFormulaComponent"),
)
async def create_component(
    body: ComponentCreate,
    formula_id: FormulaId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ComponentView:
    if body.valid_to is not None and body.valid_to <= body.valid_from:
        raise _invalid("valid_to", "valid_to must be after valid_from.")
    async with transaction(ROLE, principal) as session:
        model_row, version, owner = await store.formula_model(
            session, formula_id, principal, data_space
        )
        if body.code not in rules.formula_parameters(model_row.formula):
            raise _invalid("code", f"The formula does not cite a parameter {body.code}.")
        if body.product_code not in _products(model_row.product_code):
            raise _invalid(
                "product_code", f"product_code is {model_row.product_code} or '*' for this model."
            )
        row = fc.FormulaComponent(
            id=uuid7(),
            org_id=principal.org_id,
            code=body.code,
            product_code=body.product_code,
            value=body.value,
            unit=body.unit,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            legal_doc_number=body.legal_doc_number,
            legal_doc_url=body.legal_doc_url,
            document_id=body.document_id,
            extracted_record_id=body.extracted_record_id,
            status="pending_review",
            note=body.note,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        # Inbox opens the formula_component HITL request for the formula's owner (UC-15-07 alt 1).
        store.emit(
            session,
            principal,
            "formula_component",
            row.id,
            "forecast.formula_component.submitted",
            formula_component_id=row.id,
            formula_model_id=model_row.id,
            model_id=owner.id,
            data_space_id=data_space,
            hitl_kind="formula_component",
            assignee_id=model_row.owner_id,
            code=row.code,
            product_code=row.product_code,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        response.headers["Location"] = f"/v1/formula-models/{model_row.id}/components"
        return _component_view(row)


async def _component_in_space(
    session: AsyncSession, component_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> fc.FormulaComponent:
    """The parameter, when a formula model of the caller's Data Space cites it.

    formula_component has no Data Space: a legal parameter is the organisation's. Certifying is
    still done from a Data Space whose formula uses the value, so a steward of another Data
    Space gets 404 as for a missing row.
    """
    row = await session.scalar(
        select(fc.FormulaComponent)
        .where(
            fc.FormulaComponent.id == component_id,
            fc.FormulaComponent.org_id == principal.org_id,
        )
        .with_for_update()
    )
    if row is not None:
        formulas = (
            await session.execute(
                select(fc.FormulaModel.formula, fc.FormulaModel.product_code)
                .join(fc.ModelVersion, fc.ModelVersion.id == fc.FormulaModel.model_version_id)
                .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
                .where(
                    fc.ForecastModel.data_space_id == data_space,
                    fc.FormulaModel.org_id == principal.org_id,
                )
            )
        ).all()
        if any(
            row.code in rules.formula_parameters(formula) and row.product_code in _products(product)
            for formula, product in formulas
        ):
            return row
    raise Problem(404, "not_found", "Formula component not found.")


@router.post(
    "/v1/formula-components/{id}:certify",
    response_model=ComponentView,
    **endpoint(
        "POST", "/v1/formula-components/{id}:certify", operation_id="certifyFormulaComponent"
    ),
)
async def certify_component(
    component_id: ComponentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[ComponentCertify, Body()] = _NO_CERTIFY,
) -> ComponentView:
    c = fc.FormulaComponent
    async with transaction(ROLE, principal) as session:
        row = await _component_in_space(session, component_id, principal, data_space)
        if row.status not in ("draft", "pending_review"):
            raise Problem(
                409, "invalid_state", f"The parameter is {row.status}, not awaiting review."
            )
        if row.created_by == principal.user_id:
            raise Problem(
                403, "policy_denied", "Whoever entered the value cannot certify it (BR-182)."
            )
        # BR-182: the open certified value is closed at this one's valid_from by the trigger, which
        # needs the new value to start later; any other certified interval must not overlap.
        same = and_(
            c.org_id == row.org_id,
            c.code == row.code,
            c.product_code == row.product_code,
            c.status == "certified",
            c.id != row.id,
        )
        current = await session.scalar(select(c).where(same, c.valid_to.is_(None)))
        if current is not None and row.valid_from <= current.valid_from:
            raise Problem(
                409,
                "invalid_state",
                "The value in force starts on or after this one; a new value must start later (BR-182).",
            )
        upper = row.valid_to
        overlap = await session.scalar(
            select(c.id).where(
                same,
                c.valid_to.is_not(None),
                c.valid_to > row.valid_from,
                c.valid_from < upper if upper is not None else true(),
            )
        )
        if overlap is not None:
            raise Problem(
                409,
                "invalid_state",
                "Another certified value of this parameter is in force in the same interval (BR-182).",
            )
        if row.status == "draft":
            # The trigger allows certified only from pending_review.
            row.status = "pending_review"
            await session.flush()
        now = store.now()
        row.status = "certified"
        row.certified_by, row.certified_at = principal.user_id, now
        row.updated_by, row.updated_at = principal.user_id, now
        if body.note and body.note.strip():
            row.note = body.note.strip() if not row.note else f"{row.note}\n{body.note.strip()}"
        store.emit(
            session,
            principal,
            "formula_component",
            row.id,
            "forecast.formula_component.certified",
            formula_component_id=row.id,
            data_space_id=data_space,
            code=row.code,
            product_code=row.product_code,
            valid_from=row.valid_from.isoformat(),
            supersedes_id=current.id if current else None,
            status=row.status,
            certified_by=principal.user_id,
        )
        await session.flush()
        await session.refresh(row)
        return _component_view(row)


async def _gate(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    formulas: list[fc.FormulaModel],
    at: datetime.datetime,
) -> list[FieldError]:
    """BR-183: what keeps a run from giving a number: the formula's metric version is no longer
    certified, or a cited parameter has no certified value in force at the period, known now."""
    c = fc.FormulaComponent
    now = store.now()
    missing: list[FieldError] = []
    for formula in formulas:
        found = await _certified_metric_version(
            session, principal, data_space, formula.metric_version_id
        )
        if found is None or not _metric_version_in_force(found[0], now):
            missing.append(
                FieldError(
                    field="metric_version_id",
                    code="not_certified",
                    message=f"{formula.product_code}: the formula's metric version is not certified.",
                )
            )
        cited = rules.formula_parameters(formula.formula)
        in_force = set(
            (
                await session.scalars(
                    select(c.code).where(
                        c.org_id == principal.org_id,
                        c.code.in_(sorted(cited)),
                        c.product_code.in_(_products(formula.product_code)),
                        c.status.in_(IN_FORCE),
                        c.valid_from <= at,
                        or_(c.valid_to.is_(None), c.valid_to > at),
                        c.certified_at <= now,
                    )
                )
            ).all()
        )
        missing.extend(
            FieldError(
                field=code,
                code="not_certified",
                message=(
                    f"{formula.product_code}: {code} has no certified value on {at.date()}; "
                    f"certification needed from the formula owner {formula.owner_id}."
                ),
            )
            for code in sorted(cited - in_force)
        )
    return missing


@router.post(
    "/v1/formula-models/{id}/runs",
    status_code=202,
    response_model=ForecastAccepted,
    **endpoint("POST", "/v1/formula-models/{id}/runs", operation_id="createFormulaRun"),
)
async def create_formula_run(
    body: FormulaRunCreate,
    formula_id: FormulaId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ForecastAccepted:
    async with transaction(ROLE, principal) as session:
        row, version, owner = await store.formula_model(session, formula_id, principal, data_space)
        codes = list(dict.fromkeys(body.product_codes or [row.product_code]))
        # Other products of the same certified formula run together (one bulletin, UC-08-08).
        siblings = {row.product_code: row}
        others = [code for code in codes if code not in siblings]
        if others:
            for sibling in (
                await session.scalars(
                    select(fc.FormulaModel)
                    .join(fc.ModelVersion, fc.ModelVersion.id == fc.FormulaModel.model_version_id)
                    .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
                    .where(
                        fc.FormulaModel.org_id == principal.org_id,
                        fc.ForecastModel.data_space_id == data_space,
                        fc.FormulaModel.metric_version_id == row.metric_version_id,
                        fc.FormulaModel.product_code.in_(others),
                        fc.ModelVersion.status.not_in(("rejected", "retired")),
                    )
                    .order_by(fc.FormulaModel.created_at.desc())
                )
            ).all():
                siblings.setdefault(sibling.product_code, sibling)
        unknown = [code for code in codes if code not in siblings]
        if unknown:
            raise _invalid(
                "product_codes",
                f"No formula model of this formula for {', '.join(unknown)} in this Data Space.",
            )
        if body.issue != "none":
            if version.status != "published":
                # UC-15-07 alt 3 / E2: unreleased or degraded output is labelled, never issued.
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"The model version is {version.status}; only a published one is issued (BR-158).",
                )
            if body.period <= store.now().date():
                raise Problem(
                    422,
                    "business_rule_violated",
                    "An issued forecast is written to the ledger before its period (BR-185).",
                )
        at = datetime.datetime.combine(body.period, datetime.time.min, tzinfo=datetime.UTC)
        missing = await _gate(session, principal, data_space, [siblings[c] for c in codes], at)
        if missing:
            raise Problem(
                422,
                "business_rule_violated",
                "CHƯA CÓ dự báo: the gate of BR-183 is not met.",
                errors=missing,
            )
        progress: dict[str, Any] = {
            "purpose": "formula_run",
            "formula_model_id": str(row.id),
            "formula_model_ids": [str(siblings[c].id) for c in codes],
            "period": body.period.isoformat(),
            "issue": body.issue,
        }
        if body.event_id is not None:
            progress["event_id"] = str(body.event_id)
        job = jobs.new_job(
            session,
            principal,
            data_space,
            metric_code=owner.metric_code,
            frequency=owner.frequency,
            horizon=1,
            scope={"product_codes": codes},
            progress=progress,
            model_version_id=version.id,
        )
        await session.flush()
        return jobs.accepted(response, job)
