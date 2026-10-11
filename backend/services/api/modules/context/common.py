"""What the context routes share: the role, the outbox, paging, name normalisation and the
reader settings of BR-163.

Every write goes to schema `context` as svc_context. Effects on other schemas (the eval set and
eval run of a data product, the inbox proposal of a submitted version) are outbox events whose
ids are reserved here, the way the agent module reserves an eval run: the owner creates the row
with that id when it consumes the event (D1.2).
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypeVar

from fastapi import Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import Select, and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, org
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PageRequest,
    decode_page_token,
    encode_page_token,
    invalid_page_token,
)
from services.api.kernel.problems import FieldError, Problem

ROLE = "svc_context"
POLL_AFTER_MS = 2_000

# app.clearance ranks (context.classification_rank).
CLEARANCE = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
# No table of the design holds a reader's clearance yet (org.app_user, org.role and
# org.data_space_member carry no classification), so every reader reads at `internal`: pages
# whose strictest source is confidential or restricted stay hidden (BR-163 fails closed).
DEFAULT_CLEARANCE = CLEARANCE["internal"]

T = TypeVar("T", bound=BaseModel)


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    return out


def emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One context.outbox row in the caller's transaction (D1 rule 8)."""
    session.add(
        context.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=_jsonable(payload),
        )
    )


def audit(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str,
    object_kind: str,
    object_id: uuid.UUID,
    after: dict[str, Any] | None = None,
) -> None:
    """Audit lives in its own database (D1.1): the row is requested through the outbox, in the
    shape the qa module uses. The reason a person gives (retire, suspend) goes here, not into the
    domain event."""
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
        before=None,
        after=after,
    )


def accepted(response: Response, location: str, row_id: uuid.UUID, status: str) -> dict[str, Any]:
    """A1.6: 202 with Location and {id, status, poll_after_ms}."""
    response.headers["Location"] = location
    return {"id": row_id, "status": status, "poll_after_ms": POLL_AFTER_MS}


def context_page(
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    page_token: Annotated[str, Query(max_length=2048)] = "",
    cursor: Annotated[
        str | None, Query(max_length=2048, description="A3's name for page_token")
    ] = None,
) -> PageRequest:
    """A1.4 paging; A3 names the token `cursor` on the context lists, so both are accepted."""
    return PageRequest(page_size=page_size, cursor=decode_page_token(cursor or page_token))


ContextPage = Annotated[PageRequest, Depends(context_page)]


async def keyset_page(
    session: AsyncSession,
    query: Select[Any],
    model: Any,
    page: PageRequest,
    view: Callable[[Any], T],
) -> tuple[list[T], str]:
    """Newest first on (created_at, id); the token is the sort key of the last row served."""
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(page.cursor["created_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(model.created_at, model.id) < after)
    query = query.order_by(model.created_at.desc(), model.id.desc()).limit(page.page_size + 1)
    rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = (
        {"created_at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)} if more else None
    )
    return [view(r) for r in rows], encode_page_token(token)


_SPACES = re.compile(r"\s+")


def normalize(phrase: str) -> str:
    """phrase_norm / term_norm: no accents (đ → d), lower case, single spaces (tab 3b D4.5)."""
    text = phrase.replace("đ", "d").replace("Đ", "D")
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return _SPACES.sub(" ", text).strip().lower()


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(title)).strip("-")
    return (slug or "trang")[:70]


def field_error(field: str, message: str, code: str = "invalid") -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code=code, message=message)],
    )


async def require_active_users(
    session: AsyncSession, principal: Principal, users: dict[str, uuid.UUID | None]
) -> None:
    """Each named user is an active person of the caller's organisation (svc_context reads org
    through org_read); 422 naming the field otherwise."""
    wanted = {k: v for k, v in users.items() if v is not None}
    if not wanted:
        return
    found = set(
        (
            await session.scalars(
                select(org.AppUser.id).where(
                    org.AppUser.id.in_(set(wanted.values())),
                    org.AppUser.org_id == principal.org_id,
                    org.AppUser.status == "active",
                )
            )
        ).all()
    )
    for name, user in wanted.items():
        if user not in found:
            raise field_error(name, f"{name} is not an active user of this organisation.")


async def readable_data_spaces(session: AsyncSession, principal: Principal) -> list[uuid.UUID]:
    """The Data Spaces the caller is a current member of, directly or through a group (BR-142),
    which is what BR-163 checks a page's sources against."""
    member = org.DataSpaceMember
    groups = select(org.UserGroupMember.group_id).where(
        org.UserGroupMember.user_id == principal.user_id
    )
    query = (
        select(member.data_space_id)
        .join(org.DataSpace, org.DataSpace.id == member.data_space_id)
        .where(
            member.org_id == principal.org_id,
            org.DataSpace.deleted_at.is_(None),
            org.DataSpace.status != "archived",
            or_(member.valid_until.is_(None), member.valid_until > func.now()),
            or_(
                and_(
                    member.principal_type.in_(("user", "agent")),
                    member.principal_id == principal.user_id,
                ),
                and_(member.principal_type == "group", member.principal_id.in_(groups)),
            ),
        )
        .distinct()
    )
    return sorted(set((await session.scalars(query)).all()), key=str)


def uuid_array(ids: Sequence[uuid.UUID]) -> str:
    return "{" + ",".join(str(i) for i in ids) + "}"


async def reader_settings(principal: Principal) -> dict[str, str]:
    """app.knowledge_reader, app.data_space_ids and app.clearance for a reader transaction
    (docs/v3/07-database.md "Kết nối từ ứng dụng"): with them svc_context sees knowledge rows only
    as the caller may (BR-163)."""
    async with transaction(ROLE, principal) as session:
        spaces = await readable_data_spaces(session, principal)
    return {
        "knowledge_reader": "on",
        "data_space_ids": uuid_array(spaces),
        "clearance": str(DEFAULT_CLEARANCE),
    }
