"""Data Spaces (tab 4 A2 · Quản trị, UC-11-08)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select, tuple_

from libs.dataagent_db.models import org
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
from services.api.modules.org.identity import ROLE, memberships
from services.api.modules.org.schemas import DataSpaceSummary, RoleRef

router = APIRouter()


@router.get(
    "/v1/data-spaces", **endpoint("GET", "/v1/data-spaces", operation_id="listMyDataSpaces")
)
async def list_my_data_spaces(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
) -> Page[DataSpaceSummary]:
    mine = memberships(principal.user_id).subquery()
    space = org.DataSpace
    query = (
        select(space)
        .where(space.id.in_(select(mine.c.data_space_id)), space.deleted_at.is_(None))
        .order_by(space.code, space.id)
        .limit(page.page_size + 1)
    )
    if page.cursor:
        try:
            after = (str(page.cursor["code"]), uuid.UUID(str(page.cursor["id"])))
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(space.code, space.id) > after)
    async with transaction(ROLE, principal) as session:
        spaces = list((await session.scalars(query)).all())
        more = len(spaces) > page.page_size
        spaces = spaces[: page.page_size]
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
            my_capabilities=sorted({c for r in roles[s.id].values() for c in r.capabilities}),
        )
        for s in spaces
    ]
    last = spaces[-1] if more else None
    return Page[DataSpaceSummary](
        items=items,
        next_page_token=encode_page_token(
            {"code": last.code, "id": str(last.id)} if last else None
        ),
    )
