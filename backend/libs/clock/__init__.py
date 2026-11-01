"""Which day it is for an organisation (GEN-21).

"This month", "this quarter" and a schedule's 07:00 are the organisation's, in its time zone
(organization.time_zone), not the server's: a server in UTC answered "tháng này" a month early
for seven hours of every month-end in Vietnam, and every schedule ran in Vietnam's time whatever
the customer's. A zone name the database holds but this host cannot resolve reads as UTC.

A Data Space's `Calendar` (its org.work_calendar, else the organisation's default one) says where
its weeks and fiscal years start: "tuần này", a cut by week, the fiscal year and quarter, and the
year a "luỹ kế"/YTD runs from. Periods are half-open date ranges, [start, end).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = ZoneInfo("UTC")


def zone_named(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


async def org_zone(session: Any, org_id: uuid.UUID | str) -> ZoneInfo:
    """The organisation's zone, read in `session` (any role with org_read)."""
    from sqlalchemy import select

    from libs.dataagent_db.models import org

    name = await session.scalar(
        select(org.Organization.time_zone).where(org.Organization.id == org_id)
    )
    return zone_named(name)


async def space_zone(session: Any, data_space_id: uuid.UUID | str) -> ZoneInfo:
    """The zone of the organisation a Data Space belongs to."""
    from sqlalchemy import select

    from libs.dataagent_db.models import org

    name = await session.scalar(
        select(org.Organization.time_zone)
        .join(org.DataSpace, org.DataSpace.org_id == org.Organization.id)
        .where(org.DataSpace.id == data_space_id)
    )
    return zone_named(name)


def today(zone: ZoneInfo, now: datetime.datetime | None = None) -> datetime.date:
    return (now or datetime.datetime.now(UTC)).astimezone(zone).date()


@dataclass(frozen=True)
class Calendar:
    # ISO weekday a week starts on, 1 = Monday.
    week_start: int = 1
    # The month a fiscal year starts in; 1 is the calendar year.
    fiscal_year_start_month: int = 1
    # "end": July 2025 to June 2026 is FY2026; "start": FY2025.
    fiscal_year_named_by: Literal["start", "end"] = "end"

    @property
    def fiscal(self) -> bool:
        return self.fiscal_year_start_month != 1


DEFAULT = Calendar()


async def space_calendar(session: Any, data_space_id: uuid.UUID | str) -> Calendar:
    """The Data Space's work calendar, else its organisation's default, else the calendar year
    with Monday weeks. Read in `session` (any role with org_read)."""
    from sqlalchemy import select

    from libs.dataagent_db.models import org

    space = await session.scalar(select(org.DataSpace).where(org.DataSpace.id == data_space_id))
    if space is None:
        return DEFAULT
    calendar = org.WorkCalendar
    row = None
    if space.work_calendar_id is not None:
        row = await session.scalar(select(calendar).where(calendar.id == space.work_calendar_id))
    if row is None:
        row = await session.scalar(
            select(calendar).where(calendar.org_id == space.org_id, calendar.is_default.is_(True))
        )
    if row is None:
        return DEFAULT
    return Calendar(row.week_start, row.fiscal_year_start_month, row.fiscal_year_named_by)


def _month_start(year: int, month: int) -> datetime.date:
    index = year * 12 + month - 1
    return datetime.date(index // 12, index % 12 + 1, 1)


def fiscal_year_of(day: datetime.date, cal: Calendar = DEFAULT) -> int:
    """The number of the fiscal year `day` is in."""
    starts_in = day.year if day.month >= cal.fiscal_year_start_month else day.year - 1
    if cal.fiscal and cal.fiscal_year_named_by == "end":
        return starts_in + 1
    return starts_in


def fiscal_year(number: int, cal: Calendar = DEFAULT) -> tuple[datetime.date, datetime.date]:
    """The days of fiscal year `number`."""
    starts_in = number - 1 if cal.fiscal and cal.fiscal_year_named_by == "end" else number
    start = _month_start(starts_in, cal.fiscal_year_start_month)
    return start, _month_start(starts_in + 1, cal.fiscal_year_start_month)


def fiscal_quarter(
    number: int, quarter: int, cal: Calendar = DEFAULT
) -> tuple[datetime.date, datetime.date]:
    """The days of quarter `quarter` (1-4) of fiscal year `number`."""
    start, _ = fiscal_year(number, cal)
    first = _month_start(start.year, start.month + 3 * (quarter - 1))
    return first, _month_start(first.year, first.month + 3)


def fiscal_quarter_of(day: datetime.date, cal: Calendar = DEFAULT) -> tuple[int, int]:
    """The fiscal year and quarter (1-4) `day` is in."""
    months = (day.month - cal.fiscal_year_start_month) % 12
    return fiscal_year_of(day, cal), months // 3 + 1


def week_of(day: datetime.date, cal: Calendar = DEFAULT) -> tuple[datetime.date, datetime.date]:
    """The days of the week `day` is in."""
    start = day - datetime.timedelta(days=(day.isoweekday() - cal.week_start) % 7)
    return start, start + datetime.timedelta(days=7)


def start_of(day: datetime.date, unit: str, cal: Calendar = DEFAULT) -> datetime.date:
    """The first day of the year, quarter, month or week `day` is in; a year and a quarter are
    the fiscal ones (the calendar's when the fiscal year starts in January)."""
    if unit == "year":
        return fiscal_year(fiscal_year_of(day, cal), cal)[0]
    if unit == "quarter":
        return fiscal_quarter(*fiscal_quarter_of(day, cal), cal)[0]
    if unit == "week":
        return week_of(day, cal)[0]
    return day.replace(day=1)
