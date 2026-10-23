"""What other api modules may ask the catalog module.

ADR-28: modules call each other through public interfaces. The conflict module ranks sources and
shows them next to the claims they back (UC-07-04, UC-07-05); it asks here instead of reading
catalog.source itself, and never sees connection details or OpenBao paths.
"""

from __future__ import annotations

import datetime
import uuid
import zoneinfo
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog, org
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.catalog import rules

# The organisation's default zone (org.organization.time_zone default) when no calendar is set.
DEFAULT_ZONE = "Asia/Ho_Chi_Minh"


@dataclass(frozen=True)
class SourceSummary:
    id: uuid.UUID
    name: str
    kind: str
    authority_level: str
    status: str


async def sources_in_data_space(
    principal: Principal, data_space: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, SourceSummary]:
    """The sources among `ids` that belong to the Data Space and are not deleted, by id."""
    wanted = set(ids)
    if not wanted:
        return {}
    src = catalog.Source
    async with transaction("svc_catalog", principal) as session:
        rows = (
            await session.execute(
                select(src.id, src.name, src.kind, src.authority_level, src.status).where(
                    src.id.in_(wanted),
                    src.org_id == principal.org_id,
                    src.data_space_id == data_space,
                    src.deleted_at.is_(None),
                )
            )
        ).all()
    return {
        row.id: SourceSummary(
            id=row.id,
            name=row.name,
            kind=row.kind,
            authority_level=row.authority_level,
            status=row.status,
        )
        for row in rows
    }


async def working_days(
    session: AsyncSession, calendar_id: uuid.UUID | None
) -> tuple[datetime.tzinfo, Callable[[datetime.date], bool]]:
    """The calendar's zone and a predicate for its non-working days (UC-04-05 step 2)."""
    if calendar_id is None:
        return zoneinfo.ZoneInfo(DEFAULT_ZONE), lambda _: False
    calendar = await session.scalar(
        select(org.WorkCalendar).where(org.WorkCalendar.id == calendar_id)
    )
    if calendar is None:
        return zoneinfo.ZoneInfo(DEFAULT_ZONE), lambda _: False
    try:
        zone: datetime.tzinfo = zoneinfo.ZoneInfo(calendar.time_zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo(DEFAULT_ZONE)
    weekend = set(calendar.weekend_days or [])
    today = datetime.datetime.now(datetime.UTC).date()
    overrides = {
        d.day: d.kind
        for d in (
            await session.scalars(
                select(org.WorkCalendarDay).where(
                    org.WorkCalendarDay.calendar_id == calendar.id,
                    org.WorkCalendarDay.day >= today - datetime.timedelta(days=1),
                )
            )
        ).all()
    }

    def skip(day: datetime.date) -> bool:
        kind = overrides.get(day)
        if kind == "holiday":
            return True
        if kind == "working_override":
            return False
        return day.isoweekday() in weekend

    return zone, skip


async def upcoming_runs(
    principal: Principal, cron: str, calendar_id: uuid.UUID | None, count: int
) -> list[datetime.datetime]:
    """The next `count` fire times of `cron` on the working days of the calendar, in its zone —
    the same reading the scan schedule shows (UC-06-03 preview). Raises rules.CronError."""
    parsed = rules.parse_cron(cron)
    async with transaction("svc_catalog", principal) as session:
        zone, skip = await working_days(session, calendar_id)
    return rules.next_runs(
        parsed, datetime.datetime.now(datetime.UTC), count, zone=zone, skip_day=skip
    )
