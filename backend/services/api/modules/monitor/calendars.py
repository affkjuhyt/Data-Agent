"""External event calendars, their periods and bulletins (UC-08-08, BR-187).

Periods are projected and detected, forecasts run and bulletins sent by the bulletin workflow
(tab 5 G8.11): the endpoints record declarations and decisions and leave the event for it. A
change to a period is announced by the table's own trigger (monitor.external_event_changed
writes monitor.external_event.changed), so these routes add no second event for it.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import monitor
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, page_request
from services.api.kernel.problems import Problem
from services.api.modules.monitor.schemas import (
    Accepted,
    BulletinState,
    BulletinView,
    CalendarCreate,
    CalendarPatch,
    CalendarStatus,
    CalendarSummary,
    CalendarView,
    EventConfirm,
    EventView,
    EventWithBulletins,
    NextEvent,
    RunBulletin,
    SubscriptionPut,
    SubscriptionView,
)
from services.api.modules.monitor.support import (
    POLL_AFTER_MS,
    ROLE,
    check_work_calendar,
    emit,
    invalid_field,
    keyset,
    next_token,
    now,
)

router = APIRouter()

CalendarId = Annotated[uuid.UUID, Path(alias="id")]
EventId = Annotated[uuid.UUID, Path(alias="id")]
UPCOMING = ("expected", "confirmed", "shifted")
FINAL = ("occurred", "cancelled")
_NO_REASON = RunBulletin()


async def _calendar(
    session: AsyncSession,
    calendar_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> monitor.ExternalCalendar:
    query = select(monitor.ExternalCalendar).where(
        monitor.ExternalCalendar.id == calendar_id,
        monitor.ExternalCalendar.org_id == principal.org_id,
        monitor.ExternalCalendar.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "External calendar not found.")
    return row


async def _event(
    session: AsyncSession, event_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> monitor.ExternalEvent:
    row = await session.scalar(
        select(monitor.ExternalEvent)
        .join(
            monitor.ExternalCalendar,
            monitor.ExternalCalendar.id == monitor.ExternalEvent.calendar_id,
        )
        .where(
            monitor.ExternalEvent.id == event_id,
            monitor.ExternalEvent.org_id == principal.org_id,
            monitor.ExternalCalendar.data_space_id == data_space,
        )
        .with_for_update(of=monitor.ExternalEvent)
    )
    if row is None:
        raise Problem(404, "not_found", "External event not found.")
    return row


def _calendar_event(
    session: AsyncSession,
    principal: Principal,
    row: monitor.ExternalCalendar,
    event_type: str,
    **extra: Any,
) -> None:
    emit(
        session,
        principal,
        "external_calendar",
        row.id,
        event_type,
        calendar_id=row.id,
        data_space_id=row.data_space_id,
        code=row.code,
        status=row.status,
        **extra,
    )


@router.get(
    "/v1/external-calendars",
    response_model=Page[CalendarSummary],
    **endpoint("GET", "/v1/external-calendars", operation_id="listExternalCalendars"),
)
async def list_calendars(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: CalendarStatus | None = None,
) -> Page[CalendarSummary]:
    cal = monitor.ExternalCalendar
    query = select(cal).where(cal.org_id == principal.org_id, cal.data_space_id == data_space)
    if status:
        query = query.where(cal.status == status)
    query = keyset(query, cal.created_at, cal.id, page)
    async with transaction(ROLE, principal) as session:
        rows, token = next_token(list((await session.scalars(query)).all()), page, "created_at")
        ids = [r.id for r in rows]
        counts = dict(
            (
                await session.execute(
                    select(monitor.EventSubscription.calendar_id, func.count())
                    .where(
                        monitor.EventSubscription.calendar_id.in_(ids),
                        monitor.EventSubscription.status == "active",
                    )
                    .group_by(monitor.EventSubscription.calendar_id)
                )
            )
            .tuples()
            .all()
        )
        upcoming = (
            await session.scalars(
                select(monitor.ExternalEvent)
                .where(
                    monitor.ExternalEvent.calendar_id.in_(ids),
                    monitor.ExternalEvent.status.in_(UPCOMING),
                    monitor.ExternalEvent.expected_at >= now(),
                )
                .order_by(monitor.ExternalEvent.calendar_id, monitor.ExternalEvent.expected_at)
                .distinct(monitor.ExternalEvent.calendar_id)
            )
        ).all()
    nexts = {e.calendar_id: NextEvent.model_validate(e, from_attributes=True) for e in upcoming}
    items = []
    for row in rows:
        item = CalendarSummary.model_validate(row, from_attributes=True)
        item.next_event = nexts.get(row.id)
        item.subscriber_count = int(counts.get(row.id, 0))
        items.append(item)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/external-calendars",
    status_code=201,
    response_model=CalendarView,
    **endpoint("POST", "/v1/external-calendars", operation_id="createExternalCalendar"),
)
async def create_calendar(
    body: CalendarCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> CalendarView:
    async with transaction(ROLE, principal) as session:
        await check_work_calendar(session, principal, body.work_calendar_id)
        row = monitor.ExternalCalendar(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            owner_id=principal.user_id,
            status="active",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(row)
        await session.flush()
        # ADR-33: the bulletin workflow creates the Temporal schedule that projects the periods.
        _calendar_event(
            session,
            principal,
            row,
            "external_calendar.created",
            detection_source_id=row.detection_source_id,
            forecast_model_ids=row.forecast_model_ids,
        )
        await session.flush()
        await session.refresh(row)
        return CalendarView.model_validate(row, from_attributes=True)


@router.patch(
    "/v1/external-calendars/{id}",
    response_model=CalendarView,
    **endpoint("PATCH", "/v1/external-calendars/{id}", operation_id="updateExternalCalendar"),
)
async def update_calendar(
    body: CalendarPatch,
    calendar_id: CalendarId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> CalendarView:
    changes = body.model_dump(exclude_unset=True)
    nullable = {"recurrence_hint", "default_local_time", "detection_source_id"}
    for field, value in changes.items():
        if value is None and field not in nullable:
            raise invalid_field(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _calendar(session, calendar_id, principal, data_space, lock=True)
        previous = row.status
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_by, row.updated_at = principal.user_id, now()
        await session.flush()
        if "pre_bulletin_local_time" in changes:
            # A3: periods still to come get their bulletin time again, by the trigger's formula;
            # each changed period is announced by the table's trigger.
            await session.execute(
                text(
                    "UPDATE monitor.external_event e"
                    " SET pre_bulletin_at = ((e.expected_at AT TIME ZONE c.timezone)::date"
                    "   - c.pre_bulletin_days_before + c.pre_bulletin_local_time) AT TIME ZONE c.timezone"
                    " FROM monitor.external_calendar c"
                    " WHERE c.id = e.calendar_id AND e.calendar_id = :calendar"
                    "   AND e.status IN ('expected', 'confirmed', 'shifted') AND e.expected_at > now()"
                ),
                {"calendar": row.id},
            )
        _calendar_event(
            session,
            principal,
            row,
            "external_calendar.updated",
            previous_status=previous,
            changed=sorted(changes),
        )
        await session.flush()
        await session.refresh(row)
        return CalendarView.model_validate(row, from_attributes=True)


@router.get(
    "/v1/external-calendars/{id}/events",
    response_model=Page[EventWithBulletins],
    **endpoint(
        "GET", "/v1/external-calendars/{id}/events", operation_id="listExternalCalendarEvents"
    ),
)
async def list_events(
    calendar_id: CalendarId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
    status: str | None = None,
) -> Page[EventWithBulletins]:
    ev = monitor.ExternalEvent
    async with transaction(ROLE, principal) as session:
        await _calendar(session, calendar_id, principal, data_space)
        query = select(ev).where(ev.calendar_id == calendar_id)
        if from_:
            query = query.where(ev.expected_at >= from_)
        if to:
            query = query.where(ev.expected_at < to)
        if status:
            query = query.where(ev.status == status)
        query = keyset(query, ev.expected_at, ev.id, page, descending=False)
        rows, token = next_token(list((await session.scalars(query)).all()), page, "expected_at")
        bulletins = (
            await session.scalars(
                select(monitor.EventBulletin).where(
                    monitor.EventBulletin.event_id.in_([r.id for r in rows])
                )
            )
        ).all()
    by_event: dict[tuple[uuid.UUID, str], BulletinState] = {
        (b.event_id, b.kind): BulletinState.model_validate(b, from_attributes=True)
        for b in bulletins
    }
    items = []
    for row in rows:
        item = EventWithBulletins.model_validate(row, from_attributes=True)
        item.pre_bulletin = by_event.get((row.id, "pre"))
        item.post_bulletin = by_event.get((row.id, "post"))
        items.append(item)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/external-events/{id}:confirm",
    response_model=EventView,
    **endpoint("POST", "/v1/external-events/{id}:confirm", operation_id="confirmExternalEvent"),
)
async def confirm_event(
    body: EventConfirm, event_id: EventId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> EventView:
    note = (body.note or "").strip() or None
    async with transaction(ROLE, principal) as session:
        row = await _event(session, event_id, principal, data_space)
        if row.status in FINAL:
            raise Problem(409, "invalid_state", f"The period is {row.status}.")
        if body.action == "shift" and body.expected_at == row.expected_at:
            raise invalid_field("expected_at", "A shift moves the period to another time.")
        if body.action == "cancel":
            row.status = "cancelled"
        else:
            if body.expected_at is not None and body.expected_at != row.expected_at:
                # The trigger keeps the old time in shifted_from and re-times the bulletin.
                row.expected_at = body.expected_at
            row.status = "shifted" if body.action == "shift" else "confirmed"
        if body.action != "cancel":
            row.confirmed_by = principal.user_id
        if note is not None:
            # The person's confirmation is this period's evidence when no source gave one.
            row.evidence = {
                **(row.evidence or {}),
                "note": note,
                "decided_by": str(principal.user_id),
            }
        await session.flush()
        await session.refresh(row)
        return EventView.model_validate(row, from_attributes=True)


@router.put(
    "/v1/external-calendars/{id}/subscription",
    response_model=SubscriptionView,
    **endpoint(
        "PUT", "/v1/external-calendars/{id}/subscription", operation_id="putCalendarSubscription"
    ),
)
async def put_subscription(
    body: SubscriptionPut,
    calendar_id: CalendarId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> SubscriptionView:
    async with transaction(ROLE, principal) as session:
        calendar = await _calendar(session, calendar_id, principal, data_space)
        if calendar.status == "retired" and body.status == "active":
            raise Problem(409, "invalid_state", "The calendar is retired.")
        row = await session.scalar(
            select(monitor.EventSubscription)
            .where(
                monitor.EventSubscription.calendar_id == calendar.id,
                monitor.EventSubscription.user_id == principal.user_id,
            )
            .with_for_update()
        )
        created = row is None
        if row is None:
            row = monitor.EventSubscription(
                id=uuid7(),
                org_id=principal.org_id,
                calendar_id=calendar.id,
                user_id=principal.user_id,
            )
            session.add(row)
        row.kinds, row.channels, row.status = list(body.kinds), list(body.channels), body.status
        row.updated_at = now()
        await session.flush()
        emit(
            session,
            principal,
            "event_subscription",
            row.id,
            "event_subscription.created" if created else "event_subscription.updated",
            subscription_id=row.id,
            calendar_id=calendar.id,
            data_space_id=data_space,
            user_id=principal.user_id,
            kinds=row.kinds,
            channels=row.channels,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return SubscriptionView.model_validate(row, from_attributes=True)


@router.get(
    "/v1/event-bulletins/{id}",
    response_model=BulletinView,
    **endpoint("GET", "/v1/event-bulletins/{id}", operation_id="getEventBulletin"),
)
async def get_bulletin(
    bulletin_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> BulletinView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(monitor.EventBulletin)
            .join(monitor.ExternalEvent, monitor.ExternalEvent.id == monitor.EventBulletin.event_id)
            .join(
                monitor.ExternalCalendar,
                monitor.ExternalCalendar.id == monitor.ExternalEvent.calendar_id,
            )
            .where(
                monitor.EventBulletin.id == bulletin_id,
                monitor.EventBulletin.org_id == principal.org_id,
                monitor.ExternalCalendar.data_space_id == data_space,
            )
        )
    if row is None:
        raise Problem(404, "not_found", "Bulletin not found.")
    view = BulletinView.model_validate(row, from_attributes=True)
    view.ledger_entry_links = [f"/v1/forecast-ledger/{i}" for i in row.ledger_entry_ids]
    return view


@router.post(
    "/v1/external-events/{id}:run-bulletin",
    status_code=202,
    response_model=Accepted,
    **endpoint(
        "POST", "/v1/external-events/{id}:run-bulletin", operation_id="runExternalEventBulletin"
    ),
)
async def run_bulletin(
    event_id: EventId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[RunBulletin, Body()] = _NO_REASON,
) -> Accepted:
    reason = (body.reason or "").strip() or None
    at = now()
    async with transaction(ROLE, principal) as session:
        event = await _event(session, event_id, principal, data_space)
        if event.status in FINAL:
            # A3: the pre-period bulletin is for a period still to come.
            raise Problem(409, "invalid_state", f"The period is {event.status}.")
        bulletin = await session.scalar(
            select(monitor.EventBulletin)
            .where(monitor.EventBulletin.event_id == event.id, monitor.EventBulletin.kind == "pre")
            .with_for_update()
        )
        manual = {
            "reason": reason,
            "requested_by": str(principal.user_id),
            "requested_at": at.isoformat(),
        }
        if bulletin is None:
            bulletin = monitor.EventBulletin(
                id=uuid7(),
                org_id=principal.org_id,
                event_id=event.id,
                kind="pre",
                status="preparing",
                content={"manual_run": manual},
            )
            session.add(bulletin)
        elif bulletin.status == "preparing":
            raise Problem(
                409, "invalid_state", "The pre-period bulletin is already being prepared."
            )
        else:
            # UNIQUE (event_id, kind) keeps one pre bulletin per period: a manual run re-issues it.
            bulletin.status, bulletin.sent_at = "preparing", None
            bulletin.ledger_entry_ids, bulletin.gate_result = [], {}
            bulletin.content, bulletin.recipients_count = {"manual_run": manual}, 0
        await session.flush()
        # The forecast agent runs, writes the ledger entries (issued_via = bulletin) and sends.
        emit(
            session,
            principal,
            "event_bulletin",
            bulletin.id,
            "event_bulletin.requested",
            bulletin_id=bulletin.id,
            event_id=event.id,
            calendar_id=event.calendar_id,
            data_space_id=data_space,
            kind="pre",
            trigger="manual",
            status=bulletin.status,
            requested_by=principal.user_id,
        )
    response.headers["Location"] = f"/v1/event-bulletins/{bulletin.id}"
    return Accepted(id=bulletin.id, status=bulletin.status, poll_after_ms=POLL_AFTER_MS)
