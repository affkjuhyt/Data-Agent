"""Creating and running Data Spaces: settings, members, sources (UC-11-08, UC-04-04).

POST and PATCH need `admin`, an organisation-wide capability, so they take no X-Data-Space. PUT
members (`steward`) and PUT sources (`admin_data`) use capabilities held inside a space: the caller
names the space in X-Data-Space and may only change that same space.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page
from services.api.kernel.problems import Problem
from services.api.modules.org.admin import grants
from services.api.modules.org.admin.common import (
    active_user,
    audit,
    emit,
    live_data_space,
    now,
    org_only,
    require_roles,
    roles_by_id,
    same_space,
)
from services.api.modules.org.admin.schemas import (
    DataSpaceCreate,
    DataSpacePatch,
    DataSpaceView,
    MemberIn,
    MembersPut,
    MemberView,
    SourcesPut,
)
from services.api.modules.org.identity import ROLE, still_valid

router = APIRouter()

SpaceId = Annotated[uuid.UUID, Path(alias="id")]

# Status moves an admin may make; opening (→ active) also stamps opened_at (BR-122).
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"onboarding", "active", "archived"}),
    "onboarding": frozenset({"draft", "active", "archived"}),
    "active": frozenset({"read_only", "archived"}),
    "read_only": frozenset({"active", "archived"}),
    "archived": frozenset(),
}


async def _calendar(session: AsyncSession, calendar_id: uuid.UUID | None) -> None:
    if calendar_id is None:
        return
    if await session.scalar(select(org.WorkCalendar.id).where(org.WorkCalendar.id == calendar_id)) is None:
        raise Problem(422, "validation_failed", "work_calendar_id names no calendar of this organisation.")


async def _steward_role(session: AsyncSession) -> org.Role | None:
    """The role a steward holds in their space: AC03 when the organisation has it, else the
    smallest role carrying `steward` and nothing organisation-wide."""
    roles = [
        r
        for r in (await session.scalars(select(org.Role).where(org.Role.capabilities.contains(["steward"])))).all()
        if not org_only(r)
    ]
    if not roles:
        return None
    return min(roles, key=lambda r: (r.code != "AC03", len(r.capabilities), r.code))


async def _seat_stewards(session: AsyncSession, principal: Principal, space: org.DataSpace) -> None:
    """The steward and backup steward act in their space through a membership (identity.py reads
    capabilities only from roles); without one nobody could manage the space's members."""
    role = await _steward_role(session)
    if role is None:
        return
    for user in {space.steward_id, space.backup_steward_id} - {None}:
        exists = await session.scalar(
            select(org.DataSpaceMember.id).where(
                org.DataSpaceMember.data_space_id == space.id,
                org.DataSpaceMember.principal_type == "user",
                org.DataSpaceMember.principal_id == user,
                org.DataSpaceMember.role_id == role.id,
            )
        )
        if exists is None:
            row = org.DataSpaceMember(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=space.id,
                principal_type="user",
                principal_id=user,
                role_id=role.id,
                source="manual",
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            session.add(row)
            await session.flush()
            emit(
                session,
                principal,
                "data_space_member",
                row.id,
                "data_space_member.created",
                data_space_id=space.id,
                member_id=row.id,
                principal_type="user",
                principal_id=user,
                role_id=role.id,
            )


def _view(space: org.DataSpace) -> DataSpaceView:
    return DataSpaceView.model_validate(space)


@router.post(
    "/v1/data-spaces",
    status_code=201,
    response_model=DataSpaceView,
    **endpoint("POST", "/v1/data-spaces", operation_id="createDataSpace"),
)
async def create_data_space(body: DataSpaceCreate, principal: CurrentPrincipal) -> DataSpaceView:
    if body.backup_steward_id is not None and body.backup_steward_id == body.steward_id:
        raise Problem(422, "business_rule_violated", "BR-143: the backup steward is another person.")
    async with transaction(ROLE, principal) as session:
        await active_user(session, body.steward_id, "steward_id")
        if body.backup_steward_id:
            await active_user(session, body.backup_steward_id, "backup_steward_id")
        await _calendar(session, body.work_calendar_id)
        space = org.DataSpace(
            id=uuid7(),
            org_id=principal.org_id,
            code=body.code,
            name=body.name,
            description=body.description,
            steward_id=body.steward_id,
            backup_steward_id=body.backup_steward_id,
            default_policy=body.default_policy.model_dump(exclude_none=True) if body.default_policy else {},
            work_calendar_id=body.work_calendar_id,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(space)
        # A duplicate code fails here as 23505 → 409 conflict (A3).
        await session.flush()
        await _seat_stewards(session, principal, space)
        emit(
            session,
            principal,
            "data_space",
            space.id,
            "data_space.created",
            data_space_id=space.id,
            code=space.code,
            status=space.status,
            steward_id=space.steward_id,
            backup_steward_id=space.backup_steward_id,
        )
        audit(
            session,
            principal,
            action="data_space.created",
            object_kind="data_space",
            object_id=space.id,
            after={"code": space.code, "steward_id": space.steward_id, "backup_steward_id": space.backup_steward_id},
        )
        await session.flush()
        await session.refresh(space)
        return _view(space)


@router.patch(
    "/v1/data-spaces/{id}",
    response_model=DataSpaceView,
    **endpoint("PATCH", "/v1/data-spaces/{id}", operation_id="updateDataSpace"),
)
async def update_data_space(space_id: SpaceId, body: DataSpacePatch, principal: CurrentPrincipal) -> DataSpaceView:
    fields = body.model_fields_set
    for required in ("name", "steward_id", "status", "default_policy"):
        if required in fields and getattr(body, required) is None:
            raise Problem(422, "validation_failed", f"{required} cannot be null.")
    async with transaction(ROLE, principal) as session:
        space = await live_data_space(session, space_id, lock=True)
        before = {"status": space.status, "steward_id": space.steward_id, "backup_steward_id": space.backup_steward_id}
        if "steward_id" in fields and body.steward_id != space.steward_id:
            assert body.steward_id is not None
            await active_user(session, body.steward_id, "steward_id")
            space.steward_id = body.steward_id
        if "backup_steward_id" in fields and body.backup_steward_id != space.backup_steward_id:
            if body.backup_steward_id is not None:
                await active_user(session, body.backup_steward_id, "backup_steward_id")
            space.backup_steward_id = body.backup_steward_id
        if space.backup_steward_id is not None and space.backup_steward_id == space.steward_id:
            raise Problem(422, "business_rule_violated", "BR-143: the backup steward is another person.")
        if "work_calendar_id" in fields:
            await _calendar(session, body.work_calendar_id)
            space.work_calendar_id = body.work_calendar_id
        if "name" in fields:
            assert body.name is not None
            space.name = body.name
        if "description" in fields:
            space.description = body.description
        if "default_policy" in fields:
            assert body.default_policy is not None
            space.default_policy = {
                **(space.default_policy or {}),
                **body.default_policy.model_dump(exclude_none=True),
            }
        if "status" in fields and body.status != space.status:
            assert body.status is not None
            if body.status not in TRANSITIONS[space.status]:
                raise Problem(409, "invalid_state", f"A Data Space cannot go from {space.status} to {body.status}.")
            space.status = body.status
            if body.status == "active" and space.opened_at is None:
                space.opened_at = now()
        if space.status == "active" and space.backup_steward_id is None:
            # BR-143: an open space always has a steward and a backup.
            raise Problem(422, "business_rule_violated", "BR-143: an active Data Space needs a backup steward.")
        space.updated_at = now()
        space.updated_by = principal.user_id
        await session.flush()
        await _seat_stewards(session, principal, space)
        emit(
            session,
            principal,
            "data_space",
            space.id,
            "data_space.updated",
            data_space_id=space.id,
            status=space.status,
            steward_id=space.steward_id,
            backup_steward_id=space.backup_steward_id,
        )
        audit(
            session,
            principal,
            action="data_space.updated",
            object_kind="data_space",
            object_id=space.id,
            before=before,
            after={"status": space.status, "steward_id": space.steward_id, "backup_steward_id": space.backup_steward_id},
        )
        await session.flush()
        await session.refresh(space)
        return _view(space)


async def _validate_members(session: AsyncSession, members: list[MemberIn]) -> dict[uuid.UUID, org.Role]:
    keys = [(m.principal_type, m.principal_id, m.role_id) for m in members]
    if len(set(keys)) != len(keys):
        raise Problem(422, "validation_failed", "A principal holds the same role twice in members.")
    stamp = now()
    for m in members:
        if m.valid_until is not None and (m.valid_until.tzinfo is None or m.valid_until <= stamp):
            raise Problem(422, "validation_failed", "valid_until is a future time with a time zone.")
    users = {m.principal_id for m in members if m.principal_type == "user"}
    agents = {m.principal_id for m in members if m.principal_type == "agent"}
    groups = {m.principal_id for m in members if m.principal_type == "group"}
    for ids, kinds in ((users, ("human", "service")), (agents, ("agent",))):
        if ids:
            found = set(
                (
                    await session.scalars(
                        select(org.AppUser.id).where(
                            org.AppUser.id.in_(ids), org.AppUser.kind.in_(kinds), org.AppUser.status == "active"
                        )
                    )
                ).all()
            )
            if ids - found:
                raise Problem(422, "validation_failed", "principal_id names no active user or agent of that type.")
    if groups:
        found = set((await session.scalars(select(org.UserGroup.id).where(org.UserGroup.id.in_(groups)))).all())
        if groups - found:
            raise Problem(422, "validation_failed", "principal_id names no group of this organisation.")
    roles = await require_roles(session, {m.role_id for m in members})
    for role in roles.values():
        if org_only(role):
            # A steward hands out Data Space roles only (tab 3b D4.1: admin, compliance, audit_read,
            # exec_vault, pack_author are organisation-wide).
            raise Problem(
                403, "policy_denied", f"Role {role.code} is organisation-wide and cannot be given in a Data Space."
            )
    return roles


def _members_query(space_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (
        select(org.DataSpaceMember)
        .where(org.DataSpaceMember.data_space_id == space_id)
        .order_by(org.DataSpaceMember.principal_type, org.DataSpaceMember.principal_id, org.DataSpaceMember.id)
    )


async def _space_holdings(
    session: AsyncSession, rows: list[tuple[str, uuid.UUID, uuid.UUID]], space_id: uuid.UUID
) -> set[grants.Holding]:
    """What each person holds in the space from (principal_type, principal_id, role_id) rows,
    plus their organisation-wide roles."""
    members = await grants.group_members(session, {p for t, p, _ in rows if t == "group"})
    out: set[grants.Holding] = set()
    for kind, principal, role in rows:
        for user in members.get(principal, set()) if kind == "group" else {principal}:
            out.add(grants.Holding(user, role, space_id))
    users = {h.user_id for h in out}
    if users:
        direct = await session.execute(
            select(org.UserRole.user_id, org.UserRole.role_id).where(
                org.UserRole.user_id.in_(users), still_valid(org.UserRole.valid_until)
            )
        )
        out |= {grants.Holding(u, r, None) for u, r in direct.tuples()}
        mapped = await session.execute(
            select(org.UserGroupMember.user_id, org.GroupMapping.role_id)
            .join(org.GroupMapping, org.GroupMapping.group_id == org.UserGroupMember.group_id)
            .where(
                org.UserGroupMember.user_id.in_(users),
                org.GroupMapping.status == "active",
                org.GroupMapping.data_space_id.is_(None),
            )
        )
        out |= {grants.Holding(u, r, None) for u, r in mapped.tuples()}
    return out


@router.put(
    "/v1/data-spaces/{id}/members",
    response_model=Page[MemberView],
    **endpoint("PUT", "/v1/data-spaces/{id}/members", operation_id="putDataSpaceMembers"),
)
async def put_members(
    space_id: SpaceId, body: MembersPut, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> Page[MemberView]:
    same_space(space_id, data_space)
    async with transaction(ROLE, principal) as session:
        await live_data_space(session, space_id, lock=True)
        new_roles = await _validate_members(session, body.members)
        existing = list((await session.scalars(_members_query(space_id))).all())
        valid = [r for r in existing if r.valid_until is None or r.valid_until > now()]
        manual = {(r.principal_type, r.principal_id, r.role_id): r for r in existing if r.source == "manual"}
        others = {(r.principal_type, r.principal_id, r.role_id) for r in existing if r.source != "manual"}
        wanted = {(m.principal_type, m.principal_id, m.role_id): m for m in body.members}

        before_rows = [(r.principal_type, r.principal_id, r.role_id) for r in valid]
        after_rows = [k for k in before_rows if k not in manual] + list(wanted)
        before = await _space_holdings(session, before_rows, space_id)
        after = await _space_holdings(session, after_rows, space_id)
        roles = {**await roles_by_id(session, {h.role_id for h in before | after}), **new_roles}
        new_conflicts = grants.conflicts(after, roles) - grants.conflicts(before, roles)
        if new_conflicts:
            raise Problem(403, "policy_denied", await grants.conflict_detail(session, new_conflicts))
        # UC-11-08 E1: the space keeps someone who can steward it.
        had_steward = any("steward" in roles[h.role_id].capabilities for h in before if h.data_space_id and h.role_id in roles)
        has_steward = any("steward" in roles[h.role_id].capabilities for h in after if h.data_space_id and h.role_id in roles)
        if had_steward and not has_steward:
            raise Problem(
                422, "business_rule_violated", "UC-11-08 E1: assign another steward before removing the last one."
            )

        removed = [r for k, r in manual.items() if k not in wanted]
        if removed:
            await session.execute(delete(org.DataSpaceMember).where(org.DataSpaceMember.id.in_([r.id for r in removed])))
        changes: list[tuple[str, org.DataSpaceMember]] = [("removed", r) for r in removed]
        stamp = now()
        for key, m in wanted.items():
            row = manual.get(key)
            if row is None:
                if key in others:
                    # Already granted by a group mapping or an access request; keep that origin.
                    continue
                row = org.DataSpaceMember(
                    id=uuid7(),
                    org_id=principal.org_id,
                    data_space_id=space_id,
                    principal_type=m.principal_type,
                    principal_id=m.principal_id,
                    role_id=m.role_id,
                    row_scope=m.row_scope,
                    valid_until=m.valid_until,
                    source="manual",
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                )
                session.add(row)
                changes.append(("created", row))
            elif row.row_scope != m.row_scope or row.valid_until != m.valid_until:
                row.row_scope = m.row_scope
                row.valid_until = m.valid_until
                row.updated_at = stamp
                row.updated_by = principal.user_id
                changes.append(("updated", row))
        await session.flush()
        for verb, row in changes:
            emit(
                session,
                principal,
                "data_space_member",
                row.id,
                f"data_space_member.{verb}",
                data_space_id=space_id,
                member_id=row.id,
                principal_type=row.principal_type,
                principal_id=row.principal_id,
                role_id=row.role_id,
                valid_until=row.valid_until,
            )
        if changes:
            audit(
                session,
                principal,
                action="data_space.members_changed",
                object_kind="data_space",
                object_id=space_id,
                after={
                    "created": [r.id for v, r in changes if v == "created"],
                    "updated": [r.id for v, r in changes if v == "updated"],
                    "removed": [r.id for v, r in changes if v == "removed"],
                },
            )
        await session.flush()
        rows = list((await session.scalars(_members_query(space_id))).all())
    return Page[MemberView](items=[MemberView.model_validate(r) for r in rows])


@router.put(
    "/v1/data-spaces/{id}/sources",
    response_model=DataSpaceView,
    **endpoint("PUT", "/v1/data-spaces/{id}/sources", operation_id="putDataSpaceSources"),
)
async def put_sources(
    space_id: SpaceId, body: SourcesPut, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DataSpaceView:
    same_space(space_id, data_space)
    if len(set(body.source_ids)) != len(body.source_ids):
        raise Problem(422, "validation_failed", "source_ids lists a source twice.")
    async with transaction(ROLE, principal) as session:
        space = await live_data_space(session, space_id)
        if space.status == "archived":
            raise Problem(409, "invalid_state", "An archived Data Space takes no sources.")
        # catalog.source.data_space_id belongs to the catalog module (D1.2): it reassigns the
        # sources, and refuses ids it does not know, when it consumes this event.
        emit(
            session,
            principal,
            "data_space",
            space.id,
            "data_space.sources_assigned",
            data_space_id=space.id,
            source_ids=sorted(body.source_ids),
        )
        audit(
            session,
            principal,
            action="data_space.sources_assigned",
            object_kind="data_space",
            object_id=space.id,
            after={"source_ids": sorted(body.source_ids)},
        )
        return _view(space)
