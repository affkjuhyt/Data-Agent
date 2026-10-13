"""Reads of Quản trị added on 12/10/2026: who can be picked (principals), what each role may do,
and who belongs to a Data Space (UC-02-03, UC-11-01, UC-11-08).

GET /v1/principals serves anyone who may ask, so it answers with as little as a picker needs: the
id, the kind and the display name of active principals of the caller's organisation, never an
email, a username, a department or an attribute (A3: "không có thuộc tính cá nhân").
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
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
from services.api.modules.org.admin.common import live_data_space, org_only, same_space
from services.api.modules.org.admin.schemas import (
    MemberView,
    PermissionMatrix,
    PermissionRoleView,
    UserGroupView,
)
from services.api.modules.org.identity import ROLE

router = APIRouter()

PrincipalKind = Literal["user", "group", "agent"]
# A user principal is a person; service accounts are not offered for sharing or membership.
_USER_KINDS: dict[str, tuple[str, ...]] = {"user": ("human",), "agent": ("agent",)}
MAX_PRINCIPALS = 100


class PrincipalRef(BaseModel):
    id: uuid.UUID
    kind: PrincipalKind
    display_name: str


class RoleView(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    capabilities: list[str]
    is_system: bool
    # True when the role carries an organisation-wide capability, so a steward cannot hand it
    # out in a Data Space (tab 3b D4.1).
    org_wide: bool


class MemberDetail(MemberView):
    # Display name of the user, group or agent; empty when it has left or been removed.
    principal_name: str
    role_code: str
    role_name: str


def _like(q: str) -> str:
    # The search text is data, not a pattern: % and _ match themselves.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get(
    "/v1/principals",
    response_model=Page[PrincipalRef],
    **endpoint("GET", "/v1/principals", operation_id="searchPrincipals"),
)
async def search_principals(
    principal: CurrentPrincipal,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    kind: PrincipalKind | None = None,
    page_size: Annotated[int, Query(ge=1, le=MAX_PRINCIPALS)] = 20,
) -> Page[PrincipalRef]:
    like = _like(q.strip())
    found: list[PrincipalRef] = []
    async with transaction(ROLE, principal) as session:
        for wanted, kinds in _USER_KINDS.items():
            if kind not in (None, wanted):
                continue
            # Matched on the display name only: a match on email or username would let a caller
            # probe values this endpoint never shows.
            rows = await session.execute(
                select(org.AppUser.id, org.AppUser.display_name)
                .where(
                    org.AppUser.org_id == principal.org_id,
                    org.AppUser.status == "active",
                    org.AppUser.kind.in_(kinds),
                    org.AppUser.display_name.ilike(like, escape="\\"),
                )
                .order_by(org.AppUser.display_name, org.AppUser.id)
                .limit(page_size)
            )
            found += [
                PrincipalRef(id=i, kind=wanted, display_name=n)  # type: ignore[arg-type]
                for i, n in rows.tuples()
            ]
        if kind in (None, "group"):
            # A directory group has no status: it exists while the identity source syncs it.
            rows = await session.execute(
                select(org.UserGroup.id, org.UserGroup.name)
                .where(
                    org.UserGroup.org_id == principal.org_id,
                    org.UserGroup.name.ilike(like, escape="\\"),
                )
                .order_by(org.UserGroup.name, org.UserGroup.id)
                .limit(page_size)
            )
            found += [PrincipalRef(id=i, kind="group", display_name=n) for i, n in rows.tuples()]
    found.sort(key=lambda p: (p.display_name.casefold(), p.kind, str(p.id)))
    return Page[PrincipalRef](items=found[:page_size])


@router.get(
    "/v1/admin/roles",
    response_model=Page[RoleView],
    **endpoint("GET", "/v1/admin/roles", operation_id="listRoles"),
)
async def list_roles(principal: CurrentPrincipal) -> Page[RoleView]:
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.scalars(
                select(org.Role)
                .where(org.Role.org_id == principal.org_id)
                .order_by(org.Role.code, org.Role.id)
            )
        ).all()
    return Page[RoleView](
        items=[
            RoleView(
                id=r.id,
                code=r.code,
                name=r.name,
                capabilities=sorted(r.capabilities),
                is_system=r.is_system,
                org_wide=org_only(r),
            )
            for r in rows
        ]
    )


@router.get(
    "/v1/admin/groups",
    response_model=Page[UserGroupView],
    **endpoint("GET", "/v1/admin/groups", operation_id="listGroups"),
)
async def list_groups(principal: CurrentPrincipal) -> Page[UserGroupView]:
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.scalars(
                select(org.UserGroup)
                .where(org.UserGroup.org_id == principal.org_id)
                .order_by(org.UserGroup.name, org.UserGroup.id)
            )
        ).all()
    return Page[UserGroupView](
        items=[
            UserGroupView(
                id=row.id, name=row.name, member_count=row.member_count, source=row.source
            )
            for row in rows
        ]
    )


@router.get(
    "/v1/data-spaces/{id}/permissions",
    response_model=PermissionMatrix,
    **endpoint("GET", "/v1/data-spaces/{id}/permissions", operation_id="getDataSpacePermissions"),
)
async def get_data_space_permissions(
    space_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> PermissionMatrix:
    same_space(space_id, data_space)
    async with transaction(ROLE, principal) as session:
        await live_data_space(session, space_id)
        roles = (
            await session.scalars(
                select(org.Role).where(org.Role.org_id == principal.org_id).order_by(org.Role.code)
            )
        ).all()
        member_counts = dict(
            await session.execute(
                select(org.DataSpaceMember.role_id, func.count(org.DataSpaceMember.id))
                .where(org.DataSpaceMember.data_space_id == space_id)
                .group_by(org.DataSpaceMember.role_id)
            )
        )
    cells = (
        "view_aggregate",
        "view_detail",
        "view_pii",
        "ask",
        "run_agent",
        "create_agent",
        "approve",
        "publish",
        "admin",
    )
    return PermissionMatrix(
        roles=[
            PermissionRoleView(
                role_id=role.id,
                name=role.name,
                member_count=int(member_counts.get(role.id, 0)),
                member_unit="người",
                cells={
                    capability: (
                        "yes"
                        if capability in role.capabilities
                        else "approval"
                        if capability in {"approve", "publish"} and "admin" in role.capabilities
                        else "no"
                    )
                    for capability in cells
                },
            )
            for role in roles
        ],
        rules={
            "row": "Theo phạm vi Data Space",
            "column": "Theo nhãn PII",
            "agent": "Theo capability của role",
        },
    )


async def _names(
    session: AsyncSession, rows: list[org.DataSpaceMember]
) -> dict[tuple[str, uuid.UUID], str]:
    users = {r.principal_id for r in rows if r.principal_type in ("user", "agent")}
    groups = {r.principal_id for r in rows if r.principal_type == "group"}
    out: dict[tuple[str, uuid.UUID], str] = {}
    if users:
        found = await session.execute(
            select(org.AppUser.id, org.AppUser.kind, org.AppUser.display_name).where(
                org.AppUser.id.in_(users)
            )
        )
        for user_id, user_kind, name in found.tuples():
            out[("agent" if user_kind == "agent" else "user", user_id)] = name
    if groups:
        named = await session.execute(
            select(org.UserGroup.id, org.UserGroup.name).where(org.UserGroup.id.in_(groups))
        )
        out |= {("group", g): n for g, n in named.tuples()}
    return out


@router.get(
    "/v1/data-spaces/{id}/members",
    response_model=Page[MemberDetail],
    **endpoint("GET", "/v1/data-spaces/{id}/members", operation_id="listDataSpaceMembers"),
)
async def list_members(
    space_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "principal_type", default="created_at asc")),
    ],
) -> Page[MemberDetail]:
    # `steward` is held inside a space: it shows that space's members only (as PUT does).
    same_space(space_id, data_space)
    member = org.DataSpaceMember
    query = keyset(
        select(member).where(member.org_id == principal.org_id, member.data_space_id == space_id),
        columns={
            "created_at": member.created_at,
            "updated_at": member.updated_at,
            "principal_type": member.principal_type,
        },
        tiebreak=member.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        await live_data_space(session, space_id)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        names = await _names(session, rows)
        roles = {
            r.id: r
            for r in (
                await session.scalars(
                    select(org.Role).where(org.Role.id.in_({r.role_id for r in rows}))
                )
            ).all()
        }
    items = []
    for row in rows:
        role = roles.get(row.role_id)
        items.append(
            MemberDetail(
                **MemberView.model_validate(row).model_dump(),
                principal_name=names.get((row.principal_type, row.principal_id), ""),
                role_code=role.code if role else "",
                role_name=role.name if role else "",
            )
        )
    return Page[MemberDetail](items=items, next_page_token=token)
