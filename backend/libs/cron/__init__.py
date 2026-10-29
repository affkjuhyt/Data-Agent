"""Cron schedules, read by croniter and nothing else.

Every service that accepts, previews or fires a schedule calls this module, so the api
cannot accept an expression the scheduler reads differently. A schedule is five fields
(minute hour day-of-month month day-of-week) or an @macro; day-of-week 0 and 7 are Sunday
and, as in Vixie cron, a day matches either day field when both are restricted.
"""

from __future__ import annotations

import datetime
import itertools
import re
from collections.abc import Callable, Iterator

from croniter import CroniterBadDateError, croniter

# A day-of-month that exists only on 29 February finds its run within this many days.
_HORIZON = datetime.timedelta(days=366 * 5)


_LAST_OR_WEEKDAY_DAY = re.compile(r"[LW]", re.IGNORECASE)
_LAST_WEEKDAY = re.compile(r"(^|,)\d*L($|,)", re.IGNORECASE)


class CronError(ValueError):
    """Not a five-field cron expression; the message says what croniter could not read."""


def check(expr: str) -> str:
    """The expression, its whitespace collapsed, when it is a schedule; CronError otherwise."""
    text = expr.strip()
    # croniter also reads a sixth field as seconds, which no scheduler here fires on.
    if not text.startswith("@") and len(text.split()) != 5:
        raise CronError("a cron expression has five fields: minute hour day month weekday")
    try:
        croniter(text)
    except ValueError as exc:  # every croniter error is one
        raise CronError(str(exc)) from exc
    if not text.startswith("@"):
        _, _, day, _, weekday = text.split()
        # croniter reads L (last) and W (nearest weekday), Temporal refuses them ("DayOfMonth is not
        # in range"): a schedule the api accepted would never fire. Names (jul, wed) are not these.
        if _LAST_OR_WEEKDAY_DAY.search(day) or _LAST_WEEKDAY.search(weekday):
            raise CronError("L and W (last day, nearest weekday) are not supported")
    return " ".join(text.split())


def matches(expr: str, at: datetime.datetime) -> bool:
    """Whether the schedule fires in the minute of `at`, read on `at`'s own clock."""
    return bool(croniter.match(check(expr), at))


def runs(
    expr: str,
    after: datetime.datetime,
    zone: datetime.tzinfo,
    *,
    skip_day: Callable[[datetime.date], bool] | None = None,
) -> Iterator[datetime.datetime]:
    """Fire times strictly after `after`, read on the wall clock of `zone`, in order.

    `skip_day(day)` True drops a day the schedule fires on (a non-working day of a work
    calendar); it is asked once per such day. A local time that does not exist (a DST gap) is
    dropped too. The walk ends after five years, or at once for a schedule that never fires.
    """
    # croniter given an aware datetime returns the wall time of a DST gap as if it existed, so
    # it walks the naive wall clock and the zone is attached afterwards.
    start = after.astimezone(zone).replace(tzinfo=None)
    walk = croniter(check(expr), start)
    while True:
        try:
            local: datetime.datetime = walk.get_next(datetime.datetime)
        except CroniterBadDateError:  # 30 February and the like
            return
        if local - start > _HORIZON:
            return
        if skip_day is not None and skip_day(local.date()):
            # Jump past the day instead of visiting each of its minutes.
            walk.set_current(
                datetime.datetime.combine(local.date(), datetime.time(23, 59)), force=True
            )
            continue
        at = local.replace(tzinfo=zone)
        if at.astimezone(datetime.UTC).astimezone(zone).replace(tzinfo=None) != local:
            continue
        yield at


def next_runs(
    expr: str,
    after: datetime.datetime,
    count: int,
    zone: datetime.tzinfo = datetime.UTC,
    *,
    skip_day: Callable[[datetime.date], bool] | None = None,
) -> list[datetime.datetime]:
    """The first `count` of `runs`; fewer when the schedule stops firing within the horizon."""
    return list(itertools.islice(runs(expr, after, zone, skip_day=skip_day), count))


def fires_per_day(expr: str) -> int:
    """How many times the schedule fires on a day it fires at all."""
    minutes, hours = croniter(check(expr)).expanded[:2]
    return (60 if minutes == ["*"] else len(minutes)) * (24 if hours == ["*"] else len(hours))
