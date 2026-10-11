"""A2 group Chính sách và Quản trị (mô hình, nhà cung cấp, cài đặt AI): the governance schema's routes."""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select, tuple_

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import governance
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.governance.schemas import (
    ModelProviderApprove,
    ModelProviderCreate,
    ModelProviderPage,
    ModelProviderPatch,
    ModelProviderView,
    ModelRouteDecisionPage,
    ModelRouteDecisionView,
)

router = APIRouter()
ROLE = "svc_governance"


def _month_start() -> datetime.datetime:
    now = datetime.datetime.now(datetime.UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _provider_cost(
    provider: governance.ModelProvider, usage: Iterable[governance.ModelUsage]
) -> decimal.Decimal:
    """Price usage without exposing provider secrets or making the client derive totals."""
    total = decimal.Decimal("0")
    price = provider.price if isinstance(provider.price, dict) else {}
    for row in usage:
        rates = price.get(row.model_name, {})
        if not isinstance(rates, dict):
            continue
        total += decimal.Decimal(str(row.input_tokens or 0)) * decimal.Decimal(
            str(rates.get("input", 0))
        )
        total += decimal.Decimal(str(row.output_tokens or 0)) * decimal.Decimal(
            str(rates.get("output", 0))
        )
    return total


@router.get(
    "/v1/admin/model-providers",
    response_model=ModelProviderPage,
    **endpoint("GET", "/v1/admin/model-providers", operation_id="listModelProviders"),
)
async def list_model_providers(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    kind: Annotated[str | None, Query(max_length=30)] = None,
    enabled: bool | None = None,
) -> ModelProviderPage:
    provider = governance.ModelProvider
    query = select(provider).where(provider.org_id == principal.org_id)
    if kind:
        query = query.where(provider.kind == kind)
    if enabled is not None:
        query = query.where(provider.enabled.is_(enabled))
    if page.cursor:
        try:
            after = (str(page.cursor["code"]), uuid.UUID(str(page.cursor["id"])))
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(provider.code, provider.id) > after)
    query = query.order_by(provider.code, provider.id).limit(page.page_size + 1)

    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        more = len(rows) > page.page_size
        rows = rows[: page.page_size]
        usage_by_provider: dict[uuid.UUID, list[governance.ModelUsage]] = {
            row.id: [] for row in rows
        }
        if rows:
            usage_rows = await session.scalars(
                select(governance.ModelUsage).where(
                    governance.ModelUsage.org_id == principal.org_id,
                    governance.ModelUsage.provider_id.in_(usage_by_provider),
                    governance.ModelUsage.called_at >= _month_start(),
                )
            )
            for usage in usage_rows:
                if usage.provider_id in usage_by_provider:
                    usage_by_provider[usage.provider_id].append(usage)

    last = rows[-1] if more else None
    return Page[ModelProviderView](
        items=[
            ModelProviderView.model_validate(row).model_copy(
                update={"monthly_cost_vnd": _provider_cost(row, usage_by_provider[row.id])}
            )
            for row in rows
        ],
        next_page_token=encode_page_token(
            {"code": last.code, "id": str(last.id)} if last else None
        ),
    )


@router.post(
    "/v1/admin/model-providers",
    response_model=ModelProviderView,
    status_code=201,
    **endpoint("POST", "/v1/admin/model-providers", operation_id="createModelProvider"),
)
async def create_model_provider(
    body: ModelProviderCreate, principal: CurrentPrincipal
) -> ModelProviderView:
    if body.kind == "frontier" and (
        body.secret_ref is None
        or body.data_region is None
        or not body.endpoint.startswith("https://")
    ):
        raise Problem(
            422,
            "validation_failed",
            "A frontier provider needs an https endpoint, secret_ref and data_region.",
        )
    async with transaction(ROLE, principal) as session:
        row = governance.ModelProvider(
            id=uuid7(),
            org_id=principal.org_id,
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            api_style=body.api_style,
            endpoint=body.endpoint,
            secret_ref=body.secret_ref,
            data_region=body.data_region,
            zero_retention=body.zero_retention,
            contract_ref=body.contract_ref,
            max_classification="public" if body.kind == "frontier" else "restricted",
            enabled=False,
            price=body.price,
            monthly_budget_vnd=body.monthly_budget_vnd,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.patch(
    "/v1/admin/model-providers/{id}",
    response_model=ModelProviderView,
    **endpoint("PATCH", "/v1/admin/model-providers/{id}", operation_id="patchModelProvider"),
)
async def patch_model_provider(
    body: ModelProviderPatch,
    provider_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> ModelProviderView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.ModelProvider)
            .where(
                governance.ModelProvider.id == provider_id,
                governance.ModelProvider.org_id == principal.org_id,
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Model provider not found.")

        changed_contract = bool(
            {"endpoint", "secret_ref", "data_region", "zero_retention"}
            & body.model_fields_set
        )
        for field in (
            "display_name",
            "endpoint",
            "secret_ref",
            "data_region",
            "zero_retention",
            "contract_ref",
            "price",
            "monthly_budget_vnd",
            "status",
        ):
            if field in body.model_fields_set:
                setattr(row, field, getattr(body, field))

        if row.kind == "frontier" and row.endpoint is not None and not row.endpoint.startswith("https://"):
            raise Problem(422, "validation_failed", "A frontier provider needs an https endpoint.")
        if row.kind == "frontier" and (
            row.secret_ref is None or row.data_region is None
        ):
            raise Problem(
                422,
                "validation_failed",
                "A frontier provider needs secret_ref and data_region.",
            )
        if row.status == "retired" and body.enabled is True:
            raise Problem(422, "business_rule_violated", "A retired provider cannot be enabled.")
        if body.enabled is True and row.kind == "frontier" and (
            row.approved_by is None or row.approved_at is None or not row.zero_retention
        ):
            raise Problem(
                422,
                "business_rule_violated",
                "A frontier provider needs compliance approval and zero retention before enabling.",
            )
        if body.enabled is True and row.approved_by == principal.user_id:
            raise Problem(
                403,
                "policy_denied",
                "The person who approved a frontier provider cannot enable it.",
            )

        if changed_contract:
            row.enabled = False
            row.approved_by = None
            row.approved_at = None
        elif body.enabled is not None:
            row.enabled = body.enabled
            if body.enabled:
                row.enabled_by = principal.user_id
        row.updated_by = principal.user_id
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.post(
    "/v1/admin/model-providers/{id}:approve",
    response_model=ModelProviderView,
    **endpoint(
        "POST",
        "/v1/admin/model-providers/{id}:approve",
        operation_id="approveModelProvider",
    ),
)
async def approve_model_provider(
    body: ModelProviderApprove,
    provider_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> ModelProviderView:
    if "compliance" not in principal.capabilities:
        raise Problem(403, "policy_denied", "Only compliance can approve a model provider.")
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.ModelProvider)
            .where(
                governance.ModelProvider.id == provider_id,
                governance.ModelProvider.org_id == principal.org_id,
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Model provider not found.")
        if row.kind != "frontier":
            raise Problem(422, "business_rule_violated", "Only frontier providers need approval.")
        if row.status == "retired":
            raise Problem(409, "invalid_state", "A retired provider cannot be approved.")
        if row.enabled and row.enabled_by == principal.user_id:
            raise Problem(
                403,
                "policy_denied",
                "The person who enabled a frontier provider cannot approve it.",
            )
        if (
            not row.endpoint.startswith("https://")
            or row.secret_ref is None
            or row.data_region is None
            or not row.zero_retention
        ):
            raise Problem(
                422,
                "business_rule_violated",
                "A frontier provider needs a safe external contract before approval.",
            )
        row.approved_by = principal.user_id
        row.approved_at = datetime.datetime.now(datetime.UTC)
        row.updated_by = principal.user_id
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.get(
    "/v1/admin/model-route-decisions",
    response_model=ModelRouteDecisionPage,
    **endpoint(
        "GET",
        "/v1/admin/model-route-decisions",
        operation_id="listModelRouteDecisions",
    ),
)
async def list_model_route_decisions(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    decision: str | None = Query(default=None, max_length=40),
    reason: str | None = Query(default=None, max_length=80),
    provider_id: uuid.UUID | None = None,
    caller_kind: str | None = Query(default=None, max_length=30),
    caller_id: uuid.UUID | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> ModelRouteDecisionPage:
    row_type = governance.ModelRouteDecision
    query = select(row_type).where(row_type.org_id == principal.org_id)
    if decision:
        query = query.where(row_type.decision == decision)
    if reason:
        query = query.where(row_type.reasons.any(reason))
    if provider_id:
        query = query.where(row_type.chosen_provider_id == provider_id)
    if caller_kind:
        query = query.where(row_type.caller_kind == caller_kind)
    if caller_id:
        query = query.where(row_type.caller_id == caller_id)
    if from_:
        query = query.where(row_type.decided_at >= from_)
    if to:
        query = query.where(row_type.decided_at < to)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(str(page.cursor["k"])),
                uuid.UUID(str(page.cursor["id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(row_type.decided_at, row_type.id) < after)
    query = query.order_by(row_type.decided_at.desc(), row_type.id.desc()).limit(
        page.page_size + 1
    )

    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return ModelRouteDecisionPage(
        items=[ModelRouteDecisionView.model_validate(row) for row in rows],
        next_page_token=encode_page_token(
            {"k": last.decided_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )
