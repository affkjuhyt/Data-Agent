"""What the monitor module's route files share: its role, the outbox writer, membership checks."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import monitor, org
from services.api.kernel.auth import Principal
from services.api.kernel.problems import FieldError, Problem

ROLE = "svc_monitor"
POLL_AFTER_MS = 2_000


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID | datetime.date | datetime.time | datetime.datetime):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def emit(
    session: AsyncSession,
    principal: Principal,
    table: str,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One monitor.outbox row in the caller's transaction (D1 rule 8): ids and states only."""
    session.add(
        monitor.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=event_type,
            payload={k: _plain(v) for k, v in payload.items()},
        )
    )


async def active_user(session: AsyncSession, principal: Principal, user_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(org.AppUser.id).where(
            org.AppUser.id == user_id,
            org.AppUser.org_id == principal.org_id,
            org.AppUser.status == "active",
        )
    )
    return found is not None


async def in_data_space(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, data_space: uuid.UUID
) -> bool:
    """A current membership of the user in the Data Space, held directly or through a group."""
    member = org.DataSpaceMember
    groups = select(org.UserGroupMember.group_id).where(org.UserGroupMember.user_id == user_id)
    found = await session.scalar(
        select(member.id)
        .where(
            member.org_id == principal.org_id,
            member.data_space_id == data_space,
            or_(member.valid_until.is_(None), member.valid_until > func.now()),
            or_(
                and_(member.principal_type == "user", member.principal_id == user_id),
                and_(member.principal_type == "group", member.principal_id.in_(groups)),
            ),
        )
        .limit(1)
    )
    return found is not None


async def check_work_calendar(
    session: AsyncSession, principal: Principal, calendar_id: uuid.UUID | None
) -> None:
    if calendar_id is None:
        return
    found = await session.scalar(
        select(org.WorkCalendar.id).where(
            org.WorkCalendar.id == calendar_id, org.WorkCalendar.org_id == principal.org_id
        )
    )
    if found is None:
        raise invalid_field("work_calendar_id", "No such work calendar.")


def invalid_field(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )
