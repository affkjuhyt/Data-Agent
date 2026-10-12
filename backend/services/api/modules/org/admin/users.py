"""Users, group mappings and emergency access (tab 4 A2 · Quản trị, UC-11-01)."""

from __future__ import annotations

import datetime
import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
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
from services.api.modules.org.admin import grants
from services.api.modules.org.admin.common import (
    active_user,
    audit,
    emit,
    now,
    org_only,
    require_roles,
    roles_by_id,
)
from services.api.modules.org.admin.schemas import (
    AdminUser,
    AdminUserDetail,
    EmergencyAccessIn,
    GrantOrigin,
    GroupMappingIn,
    GroupMappingSet,
    GroupMappingsPut,
    GroupMappingView,
    SpaceRoles,
    UserRoleView,
)
from services.api.modules.org.identity import ROLE
from services.api.modules.org.schemas import RoleRef

router = APIRouter()

# BR-44: emergency access lasts at most 24 hours.
EMERGENCY_MAX = datetime.timedelta(hours=24)


def _summary(
    user: org.AppUser, granted: list[GrantOrigin], codes: dict[uuid.UUID, str]
) -> AdminUser:
    org_roles: dict[uuid.UUID, RoleRef] = {}
    by_space: dict[uuid.UUID, dict[uuid.UUID, RoleRef]] = defaultdict(dict)
    for g in granted:
        if g.data_space_id is None:
            org_roles[g.role.id] = g.role
        else:
            by_space[g.data_space_id][g.role.id] = g.role
    view = AdminUser.model_validate(user, from_attributes=True)
    view.org_roles = sorted(org_roles.values(), key=lambda r: r.code)
    view.data_space_roles = sorted(
        (
            SpaceRoles(
                data_space_id=space,
                data_space_code=codes.get(space, ""),
                roles=sorted(roles.values(), key=lambda r: r.code),
            )
            for space, roles in by_space.items()
        ),
        key=lambda s: s.data_space_code,
    )
    return view


async def _space_codes(
    session: AsyncSession, granted: dict[uuid.UUID, list[GrantOrigin]]
) -> dict[uuid.UUID, str]:
    spaces = {g.data_space_id for gs in granted.values() for g in gs if g.data_space_id}
    if not spaces:
        return {}
    rows = await session.execute(
        select(org.DataSpace.id, org.DataSpace.code).where(org.DataSpace.id.in_(spaces))
    )
    # dict(result) would take Result.keys() for a mapping's and fail: the rows are a list first.
    return dict(rows.tuples().all())


@router.get(
    "/v1/admin/users",
    response_model=Page[AdminUser],
    **endpoint("GET", "/v1/admin/users", operation_id="listAdminUsers"),
)
async def list_users(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "username",
                "display_name",
                "kind",
                "status",
                "created_at",
                "updated_at",
                default="username asc",
            )
        ),
    ],
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    group: Annotated[str | None, Query(description="A user_group id or name")] = None,
) -> Page[AdminUser]:
    user = org.AppUser
    query = select(user).where(user.org_id == principal.org_id)
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(user.username.ilike(like), user.display_name.ilike(like), user.email.ilike(like))
        )
    if kind:
        query = query.where(user.kind == kind)
    if status:
        query = query.where(user.status == status)
    if group:
        try:
            by = org.UserGroup.id == uuid.UUID(group)
        except ValueError:
            by = org.UserGroup.name == group
        query = query.where(
            user.id.in_(
                select(org.UserGroupMember.user_id)
                .join(org.UserGroup, org.UserGroup.id == org.UserGroupMember.group_id)
                .where(by)
            )
        )
    query = keyset(
        query,
        columns={
            "username": user.username,
            "display_name": user.display_name,
            "kind": user.kind,
            "status": user.status,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        },
        tiebreak=user.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        granted = await grants.grants_for(session, [r.id for r in rows])
        codes = await _space_codes(session, granted)
    return Page[AdminUser](
        items=[_summary(r, granted[r.id], codes) for r in rows], next_page_token=token
    )


@router.get(
    "/v1/admin/users/{id}",
    response_model=AdminUserDetail,
    **endpoint("GET", "/v1/admin/users/{id}", operation_id="getAdminUser"),
)
async def get_user(
    user_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal
) -> AdminUserDetail:
    async with transaction(ROLE, principal) as session:
        user = await session.scalar(
            select(org.AppUser).where(
                org.AppUser.id == user_id, org.AppUser.org_id == principal.org_id
            )
        )
        if user is None:
            raise Problem(404, "not_found", "User not found.")
        granted = await grants.grants_for(session, [user.id])
        codes = await _space_codes(session, granted)
        groups = await grants.groups_of(session, user.id)
    summary = _summary(user, granted[user.id], codes)
    return AdminUserDetail(
        **summary.model_dump(),
        attributes=user.attributes or {},
        groups=groups,
        grants=granted[user.id],
    )


# ── group mappings ──


def _key(m: GroupMappingIn | org.GroupMapping) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    return (m.group_id, m.role_id, m.data_space_id)


async def _validate_mappings(
    session: AsyncSession, mappings: list[GroupMappingIn]
) -> dict[uuid.UUID, org.Role]:
    keys = [_key(m) for m in mappings]
    if len(set(keys)) != len(keys):
        raise Problem(
            422, "validation_failed", "A group, role and Data Space appear twice in mappings."
        )
    groups = {m.group_id for m in mappings}
    found = set(
        (await session.scalars(select(org.UserGroup.id).where(org.UserGroup.id.in_(groups)))).all()
    )
    if groups - found:
        raise Problem(422, "validation_failed", "group_id names no group of this organisation.")
    spaces = {m.data_space_id for m in mappings if m.data_space_id}
    found = set(
        (
            await session.scalars(
                select(org.DataSpace.id).where(
                    org.DataSpace.id.in_(spaces), org.DataSpace.deleted_at.is_(None)
                )
            )
        ).all()
    )
    if spaces - found:
        raise Problem(
            422, "validation_failed", "data_space_id names no Data Space of this organisation."
        )
    roles = await require_roles(session, {m.role_id for m in mappings})
    for m in mappings:
        if m.data_space_id is not None and org_only(roles[m.role_id]):
            # identity.py drops these capabilities inside a space; a mapping that would carry
            # them there is refused rather than silently granting less than it says.
            raise Problem(
                422,
                "business_rule_violated",
                f"Role {roles[m.role_id].code} carries organisation-wide capabilities; map it without a Data Space.",
            )
    return roles


async def _materialise(session: AsyncSession, principal: CurrentPrincipal) -> None:
    """Keep user_role (org-wide) and data_space_member (per space) rows of source group_mapping
    equal to what the active mappings grant: AC-11-01-01 expects one user_role row per member."""
    mappings = list(
        (
            await session.scalars(
                select(org.GroupMapping).where(org.GroupMapping.status == "active")
            )
        ).all()
    )
    members = await grants.group_members(session, {m.group_id for m in mappings})
    wanted_roles: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
    wanted_members: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], org.GroupMapping] = {}
    for m in mappings:
        if m.data_space_id is None:
            for user in sorted(members.get(m.group_id, ())):
                wanted_roles.setdefault((user, m.role_id), m.id)
        else:
            wanted_members[(m.data_space_id, m.group_id, m.role_id)] = m

    existing = list(
        (
            await session.scalars(
                select(org.UserRole).where(org.UserRole.source == "group_mapping")
            )
        ).all()
    )
    have = {(r.user_id, r.role_id): r for r in existing}
    stale = [r.id for k, r in have.items() if k not in wanted_roles]
    if stale:
        await session.execute(delete(org.UserRole).where(org.UserRole.id.in_(stale)))
    for (user, role), mapping_id in wanted_roles.items():
        row = have.get((user, role))
        if row is None:
            session.add(
                org.UserRole(
                    id=uuid7(),
                    org_id=principal.org_id,
                    user_id=user,
                    role_id=role,
                    source="group_mapping",
                    source_ref=mapping_id,
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                )
            )
        elif row.source_ref != mapping_id:
            row.source_ref = mapping_id
            row.updated_by = principal.user_id

    member = org.DataSpaceMember
    rows = list(
        (await session.scalars(select(member).where(member.principal_type == "group"))).all()
    )
    taken = {(r.data_space_id, r.principal_id, r.role_id): r for r in rows}
    stale = [
        r.id for k, r in taken.items() if r.source == "group_mapping" and k not in wanted_members
    ]
    if stale:
        await session.execute(delete(member).where(member.id.in_(stale)))
    for (space, group, role), m in wanted_members.items():
        held = taken.get((space, group, role))
        if held is None:
            session.add(
                member(
                    id=uuid7(),
                    org_id=principal.org_id,
                    data_space_id=space,
                    principal_type="group",
                    principal_id=group,
                    role_id=role,
                    row_scope=m.row_scope,
                    source="group_mapping",
                    source_ref=m.id,
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                )
            )
        elif held.source == "group_mapping":
            held.row_scope = m.row_scope
            held.source_ref = m.id
            held.updated_by = principal.user_id
        # A manual or access-request row for the same group and role already grants it.


@router.put(
    "/v1/admin/group-mappings",
    response_model=GroupMappingSet,
    **endpoint("PUT", "/v1/admin/group-mappings", operation_id="putGroupMappings"),
)
async def put_group_mappings(
    body: GroupMappingsPut, principal: CurrentPrincipal
) -> GroupMappingSet:
    async with transaction(ROLE, principal) as session:
        await _validate_mappings(session, body.mappings)
        current = {
            _key(m): m
            for m in (await session.scalars(select(org.GroupMapping).with_for_update())).all()
        }
        before_active = await grants.active_mappings(session)
        after_active = {_key(m) for m in body.mappings if m.status == "active"}
        members = await grants.group_members(session)
        base = await grants.base_holdings(session, members)
        before = base | grants.from_mappings(before_active, members)
        after = base | grants.from_mappings(after_active, members)
        roles = await roles_by_id(session, {h.role_id for h in before | after})
        new_conflicts = grants.conflicts(after, roles) - grants.conflicts(before, roles)
        if new_conflicts:
            raise Problem(
                422, "business_rule_violated", await grants.conflict_detail(session, new_conflicts)
            )
        added = sorted({h.user_id for h in after - before})
        removed = sorted({h.user_id for h in before - after})
        if body.preview:
            items = [
                GroupMappingView(
                    id=current[_key(m)].id if _key(m) in current else uuid7(),
                    group_id=m.group_id,
                    role_id=m.role_id,
                    data_space_id=m.data_space_id,
                    row_scope=m.row_scope,
                    status=m.status,
                    created_at=current[_key(m)].created_at if _key(m) in current else now(),
                    updated_at=now(),
                )
                for m in body.mappings
            ]
            return GroupMappingSet(
                items=items, applied=False, users_added=added, users_removed=removed
            )

        stamp = now()
        wanted = {_key(m): m for m in body.mappings}
        changed: list[org.GroupMapping] = []
        for key, m in wanted.items():
            row = current.get(key)
            if row is None:
                row = org.GroupMapping(
                    id=uuid7(),
                    org_id=principal.org_id,
                    group_id=m.group_id,
                    role_id=m.role_id,
                    data_space_id=m.data_space_id,
                    row_scope=m.row_scope,
                    status=m.status,
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                )
                session.add(row)
                changed.append(row)
            elif row.status != m.status or row.row_scope != m.row_scope:
                row.status = m.status
                row.row_scope = m.row_scope
                row.updated_at = stamp
                row.updated_by = principal.user_id
                changed.append(row)
        for key, row in current.items():
            if key not in wanted and row.status != "disabled":
                row.status = "disabled"
                row.updated_at = stamp
                row.updated_by = principal.user_id
                changed.append(row)
        await session.flush()
        await _materialise(session, principal)
        for row in changed:
            # The directory sync consumes it and syncs at once (UC-11-01 step 3).
            emit(
                session,
                principal,
                "group_mapping",
                row.id,
                "group_mapping.changed",
                group_mapping_id=row.id,
                group_id=row.group_id,
                role_id=row.role_id,
                data_space_id=row.data_space_id,
                status=row.status,
            )
            audit(
                session,
                principal,
                action="group_mapping.changed",
                object_kind="group_mapping",
                object_id=row.id,
                after={
                    "group_id": row.group_id,
                    "role_id": row.role_id,
                    "data_space_id": row.data_space_id,
                    "status": row.status,
                },
            )
        await session.flush()
        rows = list(
            (
                await session.scalars(
                    select(org.GroupMapping).order_by(
                        org.GroupMapping.created_at, org.GroupMapping.id
                    )
                )
            ).all()
        )
    return GroupMappingSet(
        items=[GroupMappingView.model_validate(r) for r in rows],
        applied=True,
        users_added=added,
        users_removed=removed,
    )


# ── emergency access ──


@router.post(
    "/v1/admin/emergency-access",
    status_code=201,
    response_model=UserRoleView,
    **endpoint("POST", "/v1/admin/emergency-access", operation_id="grantEmergencyAccess"),
)
async def grant_emergency_access(
    body: EmergencyAccessIn, principal: CurrentPrincipal
) -> UserRoleView:
    start = now()
    valid_until = body.valid_until or start + EMERGENCY_MAX
    if valid_until.tzinfo is None:
        raise Problem(422, "validation_failed", "valid_until needs a time zone.")
    if valid_until <= start or valid_until > start + EMERGENCY_MAX + datetime.timedelta(minutes=1):
        raise Problem(
            422, "business_rule_violated", "BR-44: emergency access lasts at most 24 hours."
        )
    if body.second_approver_id in (principal.user_id, body.user_id):
        raise Problem(
            422,
            "business_rule_violated",
            "BR-44: the second approver is neither the grantor nor the grantee.",
        )
    async with transaction(ROLE, principal) as session:
        await active_user(session, body.user_id, "user_id", kinds=("human", "service"))
        await active_user(session, body.second_approver_id, "second_approver_id")
        await require_roles(session, {body.role_id})
        row = await session.scalar(
            select(org.UserRole)
            .where(
                org.UserRole.user_id == body.user_id,
                org.UserRole.role_id == body.role_id,
                org.UserRole.source == "emergency",
            )
            .with_for_update()
        )
        if row is None:
            row = org.UserRole(
                id=uuid7(),
                org_id=principal.org_id,
                user_id=body.user_id,
                role_id=body.role_id,
                source="emergency",
                created_by=principal.user_id,
            )
            session.add(row)
        row.valid_until = valid_until
        row.approved_by = body.second_approver_id
        row.updated_by = principal.user_id
        row.updated_at = start
        await session.flush()
        emit(
            session,
            principal,
            "user_role",
            row.id,
            "user_role.granted",
            user_role_id=row.id,
            user_id=row.user_id,
            role_id=row.role_id,
            source="emergency",
            valid_until=valid_until,
        )
        # The reason is the point of an emergency grant's audit row (UC-11-01 1a).
        audit(
            session,
            principal,
            action="emergency_access.granted",
            object_kind="user_role",
            object_id=row.id,
            after={
                "user_id": row.user_id,
                "role_id": row.role_id,
                "valid_until": valid_until,
                "approved_by": body.second_approver_id,
                "reason": body.reason,
            },
        )
        await session.flush()
        await session.refresh(row)
        return UserRoleView.model_validate(row)
