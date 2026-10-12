"""Data Spaces (tab 4 A2 · Quản trị, UC-11-08)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

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
from services.api.modules.org.identity import ORG_ONLY_CAPABILITIES, ROLE, memberships
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
) -> Page[DataSpaceSummary]:
    mine = memberships(principal.user_id).subquery()
    space = org.DataSpace
    query = keyset(
        select(space).where(space.id.in_(select(mine.c.data_space_id)), space.deleted_at.is_(None)),
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
        )
        for s in spaces
    ]
    return Page[DataSpaceSummary](items=items, next_page_token=token)
