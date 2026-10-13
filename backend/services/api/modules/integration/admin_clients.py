"""GET /v1/admin/clients (UC-12-01, UC-12-04, added 12/10/2026): every REST and MCP client of the
organisation with its scope, its calls over the last 24 hours, expiry and status.

The row never carries a secret: the client secret is shown once at issue and only its OpenBao
path is stored, which is not part of the view either (ApiClientView).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from libs.dataagent_db.models import integration as ig
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal
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
from services.api.modules.integration.schemas import ApiClientView

router = APIRouter()
ROLE = "svc_integration"
USAGE_WINDOW = datetime.timedelta(hours=24)

ClientStatus = Literal["pending_owner", "active", "paused", "revoked", "expired"]


class ApiClientListed(ApiClientView):
    # Summed from usage_counter over the last 24 hours; the per-hour detail is .../usage.
    calls_24h: int = 0
    errors_24h: int = 0


@router.get(
    "/v1/admin/clients",
    response_model=Page[ApiClientListed],
    **endpoint("GET", "/v1/admin/clients", operation_id="listApiClients"),
)
async def list_api_clients(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "updated_at", "expires_at", "name", default="created_at desc")
        ),
    ],
    status: Annotated[ClientStatus | None, Query()] = None,
    kind: Annotated[Literal["rest", "mcp"] | None, Query()] = None,
) -> Page[ApiClientListed]:
    c = ig.ApiClient
    query = select(c).where(c.org_id == principal.org_id)
    if status is not None:
        query = query.where(c.status == status)
    if kind is not None:
        query = query.where(c.kind == kind)
    query = keyset(
        query,
        columns={
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "expires_at": c.expires_at,
            "name": c.name,
        },
        tiebreak=c.id,
        order=order,
        page=page,
    )
    since = datetime.datetime.now(datetime.UTC) - USAGE_WINDOW
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        usage: dict[uuid.UUID, tuple[int, int]] = {}
        if rows:
            uc = ig.UsageCounter
            found = await session.execute(
                select(uc.api_client_id, func.sum(uc.calls), func.sum(uc.errors))
                .where(uc.api_client_id.in_([r.id for r in rows]), uc.minute >= since)
                .group_by(uc.api_client_id)
            )
            usage = {i: (int(calls or 0), int(errors or 0)) for i, calls, errors in found.tuples()}
    items = []
    for row in rows:
        calls, errors = usage.get(row.id, (0, 0))
        items.append(
            ApiClientListed(
                **ApiClientView.model_validate(row).model_dump(),
                calls_24h=calls,
                errors_24h=errors,
            )
        )
    return Page[ApiClientListed](items=items, next_page_token=token)
