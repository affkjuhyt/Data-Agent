"""What other api modules may ask of the org module.

`capabilities_of` answers for a person who is not the caller: an MCP client acting for its
owner (BR-48) gets exactly what that owner holds in the named Data Space, resolved the same way
as for a signed-in request (identity.OrgIdentityResolver), so a client never holds more than its
owner and loses it the moment the owner does.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import org
from services.api.kernel.auth import DataSpaceNotFound, Principal
from services.api.modules.org.admin.common import audit, emit, org_only
from services.api.modules.org.identity import ROLE, OrgIdentityResolver, my_groups, still_valid

logger = logging.getLogger(__name__)


async def capabilities_of(
    org_id: uuid.UUID, user_id: uuid.UUID, data_space_id: uuid.UUID
) -> frozenset[str] | None:
    """The person's capabilities in the Data Space; None when they are not an active user of the
    organisation or not a current member of the space."""
    async with scoped_session(ROLE, org_id) as session:
        external_id = await session.scalar(
            select(org.AppUser.external_id).where(
                org.AppUser.id == user_id, org.AppUser.status == "active"
            )
        )
    if external_id is None:
        return None
    try:
        identity = await OrgIdentityResolver().resolve(
            org_id=org_id, external_id=external_id, data_space=str(data_space_id)
        )
    except DataSpaceNotFound:
        return None
    return identity.capabilities if identity else None


async def _ask_role(session: AsyncSession, held: frozenset[str]) -> org.Role | None:
    """AC01 when it fits, else the smallest role carrying `ask`: one whose capabilities the
    owner holds in the space (`held`) and nothing organisation-wide, so the agent asks and
    never does more than its owner may."""
    roles = [
        r
        for r in (
            await session.scalars(
                select(org.Role).where(
                    func.array_position(org.Role.capabilities, "ask").isnot(None)
                )
            )
        ).all()
        if not org_only(r) and set(r.capabilities) <= held
    ]
    if not roles:
        return None
    return min(roles, key=lambda r: (r.code != "AC01", len(r.capabilities), r.code))


async def _owner_grant(
    session: AsyncSession, space: org.DataSpace, owner_id: uuid.UUID
) -> tuple[bool, dict[str, Any] | None, frozenset[str]]:
    """(may the agent be seated, its row scope, the capabilities its owner holds in the space):
    never more than its owner reads. Several different scoped grants are a union one membership
    row cannot hold, so the agent is left for the steward to grant. At run time the worker also
    holds the agent to its owner's current grant (query/scope.agent_owner)."""
    member = org.DataSpaceMember
    rows = (
        await session.execute(
            select(member.row_scope, org.Role.capabilities)
            .join(org.Role, org.Role.id == member.role_id)
            .where(
                member.data_space_id == space.id,
                still_valid(member.valid_until),
                or_(
                    and_(member.principal_type == "user", member.principal_id == owner_id),
                    and_(
                        member.principal_type == "group",
                        member.principal_id.in_(my_groups(owner_id)),
                    ),
                ),
            )
        )
    ).all()
    held = frozenset(c for _, capabilities in rows for c in (capabilities or []))
    if space.steward_id == owner_id:
        return True, None, held | {"ask"}
    scopes = [scope for scope, capabilities in rows if "ask" in (capabilities or [])]
    if not scopes:
        return False, None, held
    if any(scope is None for scope in scopes):
        return True, None, held
    distinct = {repr(sorted(scope.items())) for scope in scopes}
    return (True, scopes[0], held) if len(distinct) == 1 else (False, None, held)


async def open_agent_account(
    principal: Principal,
    *,
    account_id: uuid.UUID,
    agent_id: uuid.UUID,
    data_space_id: uuid.UUID,
    code: str,
    name: str,
) -> None:
    """The agent's account (app_user kind agent, owned by its creator, BR-147) and its ask
    membership in the agent's Data Space; a second call for the same account does nothing.

    A space with no member yet is the open pilot state (worker query/scope.py): seating the
    agent would close it to everyone else, so the account is created and not seated."""
    async with scoped_session(ROLE, principal.org_id, principal.user_id) as session:
        if await session.get(org.AppUser, account_id) is not None:
            return
        session.add(
            org.AppUser(
                id=account_id,
                org_id=principal.org_id,
                kind="agent",
                username=f"agent-{code}-{str(account_id)[-8:]}",
                display_name=name,
                owner_user_id=principal.user_id,
                default_data_space_id=data_space_id,
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
        )
        await session.flush()
        space = await session.get(org.DataSpace, data_space_id)
        members = await session.scalar(
            select(func.count())
            .select_from(org.DataSpaceMember)
            .where(org.DataSpaceMember.data_space_id == data_space_id)
        )
        if space is None or not members:
            return
        seated, scope, held = await _owner_grant(session, space, principal.user_id)
        role = await _ask_role(session, held)
        if role is None or not seated:
            logger.info("agent %s not seated in Data Space %s", agent_id, data_space_id)
            return
        row = org.DataSpaceMember(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space_id,
            principal_type="agent",
            principal_id=account_id,
            role_id=role.id,
            row_scope=scope,
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
            data_space_id=data_space_id,
            member_id=row.id,
            principal_type="agent",
            principal_id=account_id,
            role_id=role.id,
        )
        audit(
            session,
            principal,
            action="data_space.members_changed",
            object_kind="data_space",
            object_id=data_space_id,
            after={"created": [row.id], "agent_id": agent_id},
        )
