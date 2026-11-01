"""Which day it is for an organisation (GEN-21).

"This month", "this quarter" and a schedule's 07:00 are the organisation's, in its time zone
(organization.time_zone), not the server's: a server in UTC answered "tháng này" a month early
for seven hours of every month-end in Vietnam, and every schedule ran in Vietnam's time whatever
the customer's. A zone name the database holds but this host cannot resolve reads as UTC.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any
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
