"""Helpers shared by the Quản trị routes: outbox events, audit requests, role and row lookups."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from services.api.kernel.auth import Principal
from services.api.kernel.problems import Problem
from services.api.modules.org.identity import ORG_ONLY_CAPABILITIES


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One org.outbox row in the caller's transaction (D1 rule 8)."""
    session.add(
        org.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=_plain(payload),
        )
    )


def audit(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str,
    object_kind: str,
    object_id: uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """UC-11-01: every permission change is audited. Audit is a separate database (D1.1), so the
    row is requested through the outbox in the shape the governance and qa triggers use."""
    emit(
        session,
        principal,
        object_kind,
        object_id,
        "audit.requested",
        action=action,
        object_kind=object_kind,
        object_id=object_id,
        actor_id=principal.user_id,
        client_id=principal.client_id,
        data_space_id=principal.data_space_id,
        before=before,
        after=after,
    )


async def roles_by_id(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, org.Role]:
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.scalars(select(org.Role).where(org.Role.id.in_(wanted)))
    return {r.id: r for r in rows}


async def require_roles(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, org.Role]:
    wanted = set(ids)
    found = await roles_by_id(session, wanted)
    if wanted - set(found):
        raise Problem(422, "validation_failed", "role_id names no role of this organisation.")
    return found


def org_only(role: org.Role) -> bool:
    return bool(set(role.capabilities) & ORG_ONLY_CAPABILITIES)


async def active_user(
    session: AsyncSession, user_id: uuid.UUID, field: str, *, kinds: tuple[str, ...] = ("human",)
) -> org.AppUser:
    """A user of the caller's organisation (RLS) that is active. FK checks bypass RLS, so a
    reference to another organisation's user is caught here, not by the database."""
    user = await session.scalar(
        select(org.AppUser).where(
            org.AppUser.id == user_id, org.AppUser.status == "active", org.AppUser.kind.in_(kinds)
        )
    )
    if user is None:
        raise Problem(422, "validation_failed", f"{field} names no active user.")
    return user


async def live_data_space(
    session: AsyncSession, space_id: uuid.UUID, *, lock: bool = False
) -> org.DataSpace:
    query = select(org.DataSpace).where(
        org.DataSpace.id == space_id, org.DataSpace.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    space = await session.scalar(query)
    if space is None:
        raise Problem(404, "not_found", "Data Space not found.")
    return space


def same_space(path_id: uuid.UUID, header_space: uuid.UUID) -> None:
    """Space-level capabilities (steward, admin_data) come from X-Data-Space; they only act on
    that same space. Another id answers as if it did not exist (A1.7)."""
    if path_id != header_space:
        raise Problem(404, "not_found", "Data Space not found.")
