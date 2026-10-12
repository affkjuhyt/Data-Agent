"""The owner's side of an API client (UC-12-01, BR-48): the clients I am responsible for, and
my consent for one an administrator granted in my name.

A client acts with its owner's rights, so one granted by someone else stays in pending_owner,
refused by /mcp, until the owner confirms it here. Nobody else can confirm it: a client of
another person answers 404, the same as one that does not exist.

Consent is to what the owner saw: :confirm requires If-Match with the client's ETag, its
updated_at as listed (`"<updated_at ISO 8601>"`). An administrator who widens the client between
the owner's look and their click changes updated_at, so the confirmation answers 412 (A1).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from sqlalchemy import select

from libs.dataagent_db import uuid7
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
from services.api.kernel.problems import Problem
from services.api.modules.integration.schemas import ApiClientView

router = APIRouter()
ROLE = "svc_integration"

PathId = Annotated[uuid.UUID, Path(alias="id")]


def etag(row: ig.ApiClient) -> str:
    return f'"{row.updated_at.isoformat()}"'


def _matches(if_match: str | None, row: ig.ApiClient) -> bool:
    # Compared as instants, not text: the JSON body spells UTC "Z", isoformat() "+00:00".
    if not if_match:
        return False
    try:
        seen = datetime.datetime.fromisoformat(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError:
        return False
    return seen.tzinfo is not None and seen == row.updated_at


@router.get(
    "/v1/me/clients",
    response_model=Page[ApiClientView],
    **endpoint("GET", "/v1/me/clients", operation_id="listMyApiClients"),
)
async def list_my_clients(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "updated_at", "expires_at", "name", default="created_at desc")
        ),
    ],
    status: Annotated[
        str | None, Query(pattern="^(pending_owner|active|paused|revoked|expired)$")
    ] = None,
) -> Page[ApiClientView]:
    c = ig.ApiClient
    query = select(c).where(c.owner_user_id == principal.user_id)
    if status is not None:
        query = query.where(c.status == status)
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
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page[ApiClientView](
        items=[ApiClientView.model_validate(r) for r in rows], next_page_token=token
    )


@router.post(
    "/v1/me/clients/{id}:confirm",
    response_model=ApiClientView,
    **endpoint("POST", "/v1/me/clients/{id}:confirm", operation_id="confirmMyApiClient"),
)
async def confirm_my_client(
    client_id: PathId,
    principal: CurrentPrincipal,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> ApiClientView:
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
        if not _matches(if_match, row):
            raise Problem(
                412, "precondition_failed", "If-Match must be the client's ETag as you saw it."
            )
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
        response.headers["ETag"] = etag(row)
        return ApiClientView.model_validate(row)
