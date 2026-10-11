"""The owner's side of an API client (UC-12-01, BR-48): the clients I am responsible for, and
my consent for one an administrator granted in my name.

A client acts with its owner's rights, so one granted by someone else stays in pending_owner,
refused by /mcp, until the owner confirms it here. Nobody else can confirm it: a client of
another person answers 404, the same as one that does not exist.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select, tuple_

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import integration as ig
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
from services.api.modules.integration.schemas import ApiClientView

router = APIRouter()
ROLE = "svc_integration"

PathId = Annotated[uuid.UUID, Path(alias="id")]


@router.get(
    "/v1/me/clients",
    response_model=Page[ApiClientView],
    **endpoint("GET", "/v1/me/clients", operation_id="listMyApiClients"),
)
async def list_my_clients(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Annotated[
        str | None, Query(pattern="^(pending_owner|active|paused|revoked|expired)$")
    ] = None,
) -> Page[ApiClientView]:
    c = ig.ApiClient
    query = (
        select(c)
        .where(c.owner_user_id == principal.user_id)
        .order_by(c.created_at.desc(), c.id.desc())
        .limit(page.page_size + 1)
    )
    if status is not None:
        query = query.where(c.status == status)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(str(page.cursor["at"])),
                uuid.UUID(str(page.cursor["id"])),
            )
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(c.created_at, c.id) < after)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page[ApiClientView](
        items=[ApiClientView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(
            {"at": last.created_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )


@router.post(
    "/v1/me/clients/{id}:confirm",
    response_model=ApiClientView,
    **endpoint("POST", "/v1/me/clients/{id}:confirm", operation_id="confirmMyApiClient"),
)
async def confirm_my_client(client_id: PathId, principal: CurrentPrincipal) -> ApiClientView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(ig.ApiClient)
            .where(ig.ApiClient.id == client_id, ig.ApiClient.owner_user_id == principal.user_id)
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "No such client of yours.")
        if row.status != "pending_owner":
            raise Problem(409, "invalid_state", f"The client is {row.status}, not pending_owner.")
        now = datetime.datetime.now(datetime.UTC)
        row.status, row.owner_confirmed_at = "active", now
        row.updated_by, row.updated_at = principal.user_id, now
        await session.flush()
        await session.refresh(row)
        session.add(
            ig.Outbox(
                id=uuid7(),
                org_id=principal.org_id,
                aggregate_type="api_client",
                aggregate_id=row.id,
                event_type="api_client.owner_confirmed",
                payload={"owner_user_id": str(principal.user_id)},
            )
        )
        return ApiClientView.model_validate(row)
