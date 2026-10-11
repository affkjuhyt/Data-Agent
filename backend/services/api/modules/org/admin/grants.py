"""Who holds which role where, with its origin: the source of GET /v1/admin/users and of the
BR-136 check (no one approves, AC03, and controls, AC06, in the same Data Space).

A role reaches a user four ways (UC-11-01 step 4): a directory group mapped to it, emergency
access, an approved access request, or a manual assignment. Group-mapped roles are read from the
active group_mapping rows themselves, not from the user_role / data_space_member rows they are
materialised into, so a disabled mapping stops counting at once (as in identity.py).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import org
from services.api.modules.org.admin.schemas import GrantOrigin, GroupRef
from services.api.modules.org.identity import still_valid
from services.api.modules.org.schemas import RoleRef


@dataclass(frozen=True)
class Holding:
    user_id: uuid.UUID
    role_id: uuid.UUID
    # None: organisation-wide.
    data_space_id: uuid.UUID | None


async def group_members(
    session: AsyncSession, group_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, set[uuid.UUID]]:
    query = select(org.UserGroupMember.group_id, org.UserGroupMember.user_id)
    if group_ids is not None:
        query = query.where(org.UserGroupMember.group_id.in_(set(group_ids)))
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for group_id, user_id in (await session.execute(query)).tuples():
        out[group_id].add(user_id)
    return out


def from_mappings(
    mappings: Iterable[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]],
    members: dict[uuid.UUID, set[uuid.UUID]],
) -> set[Holding]:
    """(group_id, role_id, data_space_id) of active mappings → what each member then holds."""
    return {
        Holding(user, role, space)
        for group, role, space in mappings
        for user in members.get(group, ())
    }


async def base_holdings(session: AsyncSession, members: dict[uuid.UUID, set[uuid.UUID]]) -> set[Holding]:
    """Everything held in the organisation except through group mappings."""
    out: set[Holding] = set()
    direct = await session.execute(
        select(org.UserRole.user_id, org.UserRole.role_id).where(
            org.UserRole.source != "group_mapping", still_valid(org.UserRole.valid_until)
        )
    )
    out |= {Holding(u, r, None) for u, r in direct.tuples()}
    rows = await session.execute(
        select(
            org.DataSpaceMember.principal_type,
            org.DataSpaceMember.principal_id,
            org.DataSpaceMember.role_id,
            org.DataSpaceMember.data_space_id,
        ).where(
            org.DataSpaceMember.source != "group_mapping",
            still_valid(org.DataSpaceMember.valid_until),
        )
    )
    for kind, principal, role, space in rows.tuples():
        users = members.get(principal, set()) if kind == "group" else {principal}
        out |= {Holding(u, role, space) for u in users}
    return out


async def active_mappings(session: AsyncSession) -> set[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]]:
    rows = await session.execute(
        select(org.GroupMapping.group_id, org.GroupMapping.role_id, org.GroupMapping.data_space_id).where(
            org.GroupMapping.status == "active"
        )
    )
    return set(rows.tuples())


def conflicts(holdings: Iterable[Holding], roles: dict[uuid.UUID, org.Role]) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """(user, Data Space) pairs breaking BR-136: an approving role (approve without compliance)
    in the space, and a control role (compliance) there or across the organisation."""
    org_control: set[uuid.UUID] = set()
    approver: set[tuple[uuid.UUID, uuid.UUID]] = set()
    control: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for h in holdings:
        role = roles.get(h.role_id)
        if role is None:
            continue
        caps = set(role.capabilities)
        if h.data_space_id is None:
            if "compliance" in caps:
                org_control.add(h.user_id)
            continue
        if "compliance" in caps:
            control.add((h.user_id, h.data_space_id))
        elif "approve" in caps:
            approver.add((h.user_id, h.data_space_id))
    return {(u, s) for u, s in approver if u in org_control or (u, s) in control}


async def conflict_detail(session: AsyncSession, pairs: set[tuple[uuid.UUID, uuid.UUID]]) -> str:
    """AC-11-01-06: the refusal names who would be in conflict, and where."""
    users = {u for u, _ in pairs}
    spaces = {s for _, s in pairs}
    names = dict(
        (await session.execute(select(org.AppUser.id, org.AppUser.username).where(org.AppUser.id.in_(users)))).tuples()
    )
    codes = dict(
        (await session.execute(select(org.DataSpace.id, org.DataSpace.code).where(org.DataSpace.id.in_(spaces)))).tuples()
    )
    listed = ", ".join(sorted(f"{names.get(u, u)} in {codes.get(s, s)}" for u, s in pairs))
    return f"BR-136: one person cannot both approve and control in the same Data Space ({listed})."


async def grants_for(session: AsyncSession, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[GrantOrigin]]:
    """Every role each user holds now, with where it comes from."""
    ids = set(user_ids)
    out: dict[uuid.UUID, list[GrantOrigin]] = defaultdict(list)
    if not ids:
        return out
    groups: dict[uuid.UUID, list[GroupRef]] = defaultdict(list)
    rows = await session.execute(
        select(org.UserGroupMember.user_id, org.UserGroup.id, org.UserGroup.name)
        .join(org.UserGroup, org.UserGroup.id == org.UserGroupMember.group_id)
        .where(org.UserGroupMember.user_id.in_(ids))
    )
    users_of_group: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    group_name: dict[uuid.UUID, str] = {}
    for user, group, name in rows.tuples():
        groups[user].append(GroupRef(id=group, name=name))
        users_of_group[group].add(user)
        group_name[group] = name

    def ref(role: org.Role) -> RoleRef:
        return RoleRef(id=role.id, code=role.code, name=role.name)

    direct = await session.execute(
        select(org.UserRole, org.Role)
        .join(org.Role, org.Role.id == org.UserRole.role_id)
        .where(
            org.UserRole.user_id.in_(ids),
            org.UserRole.source != "group_mapping",
            still_valid(org.UserRole.valid_until),
        )
    )
    for grant, role in direct.tuples():
        out[grant.user_id].append(
            GrantOrigin(
                role=ref(role),
                data_space_id=None,
                source=grant.source,
                source_ref=grant.source_ref,
                valid_until=grant.valid_until,
                approved_by=grant.approved_by,
            )
        )
    if users_of_group:
        mapped = await session.execute(
            select(org.GroupMapping, org.Role)
            .join(org.Role, org.Role.id == org.GroupMapping.role_id)
            .outerjoin(org.DataSpace, org.DataSpace.id == org.GroupMapping.data_space_id)
            .where(
                org.GroupMapping.group_id.in_(set(users_of_group)),
                org.GroupMapping.status == "active",
                org.DataSpace.deleted_at.is_(None),
            )
        )
        for mapping, role in mapped.tuples():
            for user in users_of_group[mapping.group_id]:
                out[user].append(
                    GrantOrigin(
                        role=ref(role),
                        data_space_id=mapping.data_space_id,
                        source="group_mapping",
                        group=GroupRef(id=mapping.group_id, name=group_name[mapping.group_id]),
                        source_ref=mapping.id,
                        row_scope=mapping.row_scope,
                    )
                )
    member = org.DataSpaceMember
    in_spaces = await session.execute(
        select(member, org.Role)
        .join(org.Role, org.Role.id == member.role_id)
        .join(org.DataSpace, org.DataSpace.id == member.data_space_id)
        .where(
            org.DataSpace.deleted_at.is_(None),
            member.source != "group_mapping",
            still_valid(member.valid_until),
            or_(
                (member.principal_type.in_(("user", "agent"))) & member.principal_id.in_(ids),
                (member.principal_type == "group") & member.principal_id.in_(set(users_of_group)),
            ),
        )
    )
    for row, role in in_spaces.tuples():
        holders = users_of_group[row.principal_id] if row.principal_type == "group" else {row.principal_id}
        for user in holders:
            out[user].append(
                GrantOrigin(
                    role=ref(role),
                    data_space_id=row.data_space_id,
                    source=row.source,
                    group=(
                        GroupRef(id=row.principal_id, name=group_name[row.principal_id])
                        if row.principal_type == "group"
                        else None
                    ),
                    source_ref=row.source_ref,
                    valid_until=row.valid_until,
                    row_scope=row.row_scope,
                )
            )
    for user in ids:
        out[user].sort(
            key=lambda g: (str(g.data_space_id or ""), g.role.code, g.source, g.group.name if g.group else "")
        )
    return out


async def groups_of(session: AsyncSession, user_id: uuid.UUID) -> list[GroupRef]:
    rows = await session.execute(
        select(org.UserGroup.id, org.UserGroup.name)
        .join(org.UserGroupMember, org.UserGroupMember.group_id == org.UserGroup.id)
        .where(org.UserGroupMember.user_id == user_id)
        .order_by(org.UserGroup.name)
    )
    return [GroupRef(id=i, name=n) for i, n in rows.tuples()]
