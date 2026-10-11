"""Who a token is in the organisation, and what they may do (tab 3b D4.1).

Organisation-wide capabilities are the union of `role.capabilities` over
  * the user's `user_role` rows still valid (emergency access expires, BR-44), and
  * the active `group_mapping` rows with no Data Space for the groups the user is in.
With a Data Space, the caller's `data_space_member` rows there are added, matched
on the user or one of the user's groups, still valid (BR-142). Naming a Data Space
the caller is not a current member of is refused like an unknown one: org-wide
roles (an admin's included) never open a space by themselves; admins manage
spaces through /v1/data-spaces/{id}, which needs no header.

Read as svc_org on every request, so a role removed in the directory or by an
admin takes effect on the next call, not at the next login.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import ColumnElement, CompoundSelect, Select, and_, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import org
from services.api.kernel.auth import DataSpaceNotFound, Identity

ROLE = "svc_org"


def still_valid(column: InstrumentedAttribute[datetime.datetime | None]) -> ColumnElement[bool]:
    return or_(column.is_(None), column > func.now())


def my_groups(user_id: uuid.UUID) -> Select[Any]:
    return select(org.UserGroupMember.group_id).where(org.UserGroupMember.user_id == user_id)


def org_wide_roles(user_id: uuid.UUID) -> list[Select[Any]]:
    """Queries for the role ids the user holds across the organisation; kept
    as a list so a caller can add one and still emit a single flat UNION ALL."""
    direct = select(org.UserRole.role_id).where(
        org.UserRole.user_id == user_id, still_valid(org.UserRole.valid_until)
    )
    by_group = select(org.GroupMapping.role_id).where(
        org.GroupMapping.group_id.in_(my_groups(user_id)),
        org.GroupMapping.data_space_id.is_(None),
        org.GroupMapping.status == "active",
    )
    return [direct, by_group]


def memberships(user_id: uuid.UUID) -> Select[Any]:
    """(data_space_id, role_id) of every Data Space membership the user holds,
    directly or through a group."""
    member = org.DataSpaceMember
    return select(member.data_space_id, member.role_id).where(
        still_valid(member.valid_until),
        or_(
            and_(member.principal_type.in_(("user", "agent")), member.principal_id == user_id),
            and_(member.principal_type == "group", member.principal_id.in_(my_groups(user_id))),
        ),
    )


async def _capabilities(session: AsyncSession, role_ids: CompoundSelect) -> frozenset[str]:
    rows = await session.execute(
        select(func.unnest(org.Role.capabilities)).where(org.Role.id.in_(role_ids)).distinct()
    )
    return frozenset(rows.scalars())


async def _data_space_id(session: AsyncSession, ref: str) -> uuid.UUID:
    # A1.2: the header carries the id or the code.
    try:
        by = org.DataSpace.id == uuid.UUID(ref)
    except ValueError:
        by = org.DataSpace.code == ref
    found = await session.scalar(
        select(org.DataSpace.id).where(by, org.DataSpace.deleted_at.is_(None))
    )
    if found is None:
        raise DataSpaceNotFound(ref)
    return found


class OrgIdentityResolver:
    async def resolve(
        self, *, org_id: uuid.UUID, external_id: str, data_space: str | None
    ) -> Identity | None:
        async with scoped_session(ROLE, org_id) as session:
            user_id = await session.scalar(
                select(org.AppUser.id).where(
                    org.AppUser.external_id == external_id, org.AppUser.status == "active"
                )
            )
            if user_id is None:
                return None
            space_id = await _data_space_id(session, data_space) if data_space else None
            roles = org_wide_roles(user_id)
            if space_id is not None:
                in_space = (
                    memberships(user_id)
                    .where(org.DataSpaceMember.data_space_id == space_id)
                    .with_only_columns(org.DataSpaceMember.role_id)
                )
                # Acting in a Data Space needs a current membership there; an
                # org-wide role does not open every space. A non-member gets the
                # same answer as for a space that does not exist (A1.7: no probing).
                if await session.scalar(select(in_space.exists())) is not True:
                    raise DataSpaceNotFound(data_space)
                roles.append(in_space)
            capabilities = await _capabilities(session, union_all(*roles))
            return Identity(user_id=user_id, capabilities=capabilities, data_space_id=space_id)
