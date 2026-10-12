"""Work calendars (UC-02-04): the working days schedules, pipelines, reports and deadlines count."""

from __future__ import annotations

import uuid
import zoneinfo
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import delete, select, tuple_, update

from libs.dataagent_db import uuid7
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
from services.api.kernel.problems import Problem
from services.api.modules.org.admin.common import audit, emit, now
from services.api.modules.org.admin.schemas import CalendarDay, CalendarPut, CalendarView
from services.api.modules.org.identity import ROLE

router = APIRouter()


def _view(calendar: org.WorkCalendar, days: list[org.WorkCalendarDay]) -> CalendarView:
    return CalendarView(
        id=calendar.id,
        code=calendar.code,
        name=calendar.name,
        time_zone=calendar.time_zone,
        weekend_days=list(calendar.weekend_days),
        is_default=calendar.is_default,
        days=[CalendarDay.model_validate(d) for d in sorted(days, key=lambda d: d.day)],
        created_at=calendar.created_at,
        updated_at=calendar.updated_at,
    )


@router.get(
    "/v1/calendars",
    response_model=Page[CalendarView],
    **endpoint("GET", "/v1/calendars", operation_id="listCalendars"),
)
async def list_calendars(
    principal: CurrentPrincipal, page: Annotated[PageRequest, Depends(page_request)]
) -> Page[CalendarView]:
    calendar = org.WorkCalendar
    query = select(calendar).where(calendar.org_id == principal.org_id)
    if page.cursor:
        try:
            after = (str(page.cursor["code"]), uuid.UUID(str(page.cursor["id"])))
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(calendar.code, calendar.id) > after)
    query = query.order_by(calendar.code, calendar.id).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        more = len(rows) > page.page_size
        rows = rows[: page.page_size]
        days: dict[uuid.UUID, list[org.WorkCalendarDay]] = defaultdict(list)
        if rows:
            for d in await session.scalars(
                select(org.WorkCalendarDay).where(
                    org.WorkCalendarDay.calendar_id.in_([r.id for r in rows])
                )
            ):
                days[d.calendar_id].append(d)
    last = rows[-1] if more else None
    return Page[CalendarView](
        items=[_view(r, days[r.id]) for r in rows],
        next_page_token=encode_page_token(
            {"code": last.code, "id": str(last.id)} if last else None
        ),
    )


def _check(body: CalendarPut) -> None:
    fields = body.model_fields_set
    for required in ("name", "time_zone", "weekend_days", "is_default", "days"):
        if required in fields and getattr(body, required) is None:
            raise Problem(422, "validation_failed", f"{required} cannot be null.")
    if body.time_zone is not None:
        try:
            zoneinfo.ZoneInfo(body.time_zone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            raise Problem(422, "validation_failed", "time_zone is not an IANA time zone.") from exc
    if body.weekend_days is not None and (
        len(set(body.weekend_days)) != len(body.weekend_days)
        or any(not 1 <= d <= 7 for d in body.weekend_days)
    ):
        raise Problem(422, "validation_failed", "weekend_days are distinct ISO weekdays, 1 to 7.")
    if body.days is not None and len({d.day for d in body.days}) != len(body.days):
        raise Problem(422, "validation_failed", "days lists a date twice.")


@router.put(
    "/v1/calendars/{id}",
    response_model=CalendarView,
    **endpoint("PUT", "/v1/calendars/{id}", operation_id="putCalendar"),
)
async def put_calendar(
    calendar_id: Annotated[uuid.UUID, Path(alias="id")],
    body: CalendarPut,
    principal: CurrentPrincipal,
) -> CalendarView:
    _check(body)
    async with transaction(ROLE, principal) as session:
        calendar = await session.scalar(
            select(org.WorkCalendar).where(org.WorkCalendar.id == calendar_id).with_for_update()
        )
        if calendar is None:
            raise Problem(404, "not_found", "Calendar not found.")
        if body.name is not None:
            calendar.name = body.name
        if body.time_zone is not None:
            calendar.time_zone = body.time_zone
        if body.weekend_days is not None:
            calendar.weekend_days = sorted(body.weekend_days)
        if body.is_default is not None and body.is_default != calendar.is_default:
            if not body.is_default:
                raise Problem(
                    422,
                    "business_rule_violated",
                    "The organisation keeps exactly one default calendar; make another one default instead.",
                )
            # One default per organisation (unique partial index): clear the old one first.
            await session.execute(
                update(org.WorkCalendar)
                .where(org.WorkCalendar.is_default.is_(True), org.WorkCalendar.id != calendar.id)
                .values(is_default=False, updated_at=now(), updated_by=principal.user_id)
            )
            await session.flush()
            calendar.is_default = True
        if body.days is not None:
            await session.execute(
                delete(org.WorkCalendarDay).where(org.WorkCalendarDay.calendar_id == calendar.id)
            )
            for d in body.days:
                session.add(
                    org.WorkCalendarDay(
                        id=uuid7(),
                        org_id=principal.org_id,
                        calendar_id=calendar.id,
                        day=d.day,
                        kind=d.kind,
                        name=d.name,
                    )
                )
        calendar.updated_at = now()
        calendar.updated_by = principal.user_id
        await session.flush()
        emit(
            session,
            principal,
            "work_calendar",
            calendar.id,
            "work_calendar.updated",
            work_calendar_id=calendar.id,
            is_default=calendar.is_default,
        )
        audit(
            session,
            principal,
            action="work_calendar.updated",
            object_kind="work_calendar",
            object_id=calendar.id,
            after={"time_zone": calendar.time_zone, "weekend_days": list(calendar.weekend_days)},
        )
        await session.flush()
        days = list(
            (
                await session.scalars(
                    select(org.WorkCalendarDay).where(
                        org.WorkCalendarDay.calendar_id == calendar.id
                    )
                )
            ).all()
        )
        await session.refresh(calendar)
        return _view(calendar, days)
