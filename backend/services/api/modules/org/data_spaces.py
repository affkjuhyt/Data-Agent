"""Data Spaces (tab 4 A2 · Quản trị, UC-11-08)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from libs.dataagent_db.models import org
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
from services.api.modules.org.identity import (
    ORG_ONLY_CAPABILITIES,
    ROLE,
    memberships,
    still_valid,
)
from services.api.modules.org.schemas import DataSpaceSummary, RoleRef

router = APIRouter()


@router.get(
    "/v1/data-spaces", **endpoint("GET", "/v1/data-spaces", operation_id="listMyDataSpaces")
)
async def list_my_data_spaces(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("code", "name", "status", "created_at", "updated_at", default="code asc")),
    ],
    scope: Annotated[
        Literal["mine", "org"],
        Query(description="mine: the spaces the caller can enter; org (admin): every space"),
    ] = "mine",
) -> Page[DataSpaceSummary]:
    # The admin's Data Space list is the organisation's: listing memberships only hid a space an
    # admin created with someone else as steward (and its detail answered 404).
    if scope == "org" and "admin" not in principal.capabilities:
        raise Problem(403, "forbidden", "Every Data Space of the organisation is listed to admin.")
    mine = memberships(principal.user_id).subquery()
    space = org.DataSpace
    listed = (
        space.org_id == principal.org_id
        if scope == "org"
        else space.id.in_(select(mine.c.data_space_id))
    )
    query = keyset(
        select(space).where(listed, space.deleted_at.is_(None)),
        columns={
            "code": space.code,
            "name": space.name,
            "status": space.status,
            "created_at": space.created_at,
            "updated_at": space.updated_at,
        },
        tiebreak=space.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        spaces, token = next_page(
            list((await session.scalars(query)).all()), order=order, page=page
        )
        roles: dict[uuid.UUID, dict[uuid.UUID, org.Role]] = defaultdict(dict)
        if spaces:
            rows = await session.execute(
                select(mine.c.data_space_id, org.Role)
                .join(org.Role, org.Role.id == mine.c.role_id)
                .where(mine.c.data_space_id.in_([s.id for s in spaces]))
            )
            for space_id, role in rows.tuples():
                roles[space_id][role.id] = role
        member = org.DataSpaceMember
        ids = [s.id for s in spaces]
        # A Result has keys(), so dict(result) reads it as a mapping: build the dicts from rows.
        counts: dict[uuid.UUID, int] = {}
        stewards: dict[uuid.UUID, str] = {}
        if ids:
            for space_id, n in (
                await session.execute(
                    select(member.data_space_id, func.count())
                    .where(member.data_space_id.in_(ids), still_valid(member.valid_until))
                    .group_by(member.data_space_id)
                )
            ).tuples():
                counts[space_id] = int(n)
            for user_id, name in (
                await session.execute(
                    select(org.AppUser.id, org.AppUser.display_name).where(
                        org.AppUser.id.in_({s.steward_id for s in spaces})
                    )
                )
            ).tuples():
                stewards[user_id] = name
    items = [
        DataSpaceSummary(
            id=s.id,
            code=s.code,
            name=s.name,
            description=s.description,
            status=s.status,
            steward_id=s.steward_id,
            opened_at=s.opened_at,
            my_roles=[
                RoleRef(id=r.id, code=r.code, name=r.name)
                for r in sorted(roles[s.id].values(), key=lambda r: r.code)
            ],
            my_capabilities=sorted(
                {c for r in roles[s.id].values() for c in r.capabilities} - ORG_ONLY_CAPABILITIES
            ),
            steward_name=stewards.get(s.steward_id),
            member_count=counts.get(s.id, 0),
        )
        for s in spaces
    ]
    return Page[DataSpaceSummary](items=items, next_page_token=token)
