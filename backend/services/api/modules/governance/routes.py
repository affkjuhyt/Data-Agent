"""A2 group Chính sách và Quản trị (mô hình, nhà cung cấp, cài đặt AI): the governance schema's routes."""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_

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
from services.api.modules.governance.schemas import ModelProviderPage, ModelProviderView

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
