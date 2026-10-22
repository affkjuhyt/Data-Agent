"""Five-field cron (minute hour day-of-month month day-of-week), the form the api accepts
(pipeline/rules.valid_cron): numbers, `*`, lists, ranges and steps. Day-of-week 0 and 7 are
Sunday. As in cron, when both day fields are restricted a day matches either of them."""

from __future__ import annotations

import datetime

_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _value(text: str) -> int:
    return _NAMES[text.lower()] if text.lower() in _NAMES else int(text)


def _field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        body, _, step = part.partition("/")
        if body in ("*", "?"):
            start, end = low, high
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = _value(a), _value(b)
        else:
            start = end = _value(body)
            if step:
                end = high
        if not (low <= start <= high and low <= end <= high):
            raise ValueError(f"{part} is outside {low}-{high}")
        values.update(range(start, end + 1, int(step) if step else 1))
    return values


def matches(expr: str, at: datetime.datetime) -> bool:
    """Whether the schedule fires in the minute of `at`."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("a cron expression has five fields")
    minute, hour, dom, month, dow = (
        _field(f, lo, hi) for f, (lo, hi) in zip(fields, _BOUNDS, strict=True)
    )
    if 7 in dow:
        dow.add(0)
    weekday = (at.weekday() + 1) % 7  # Monday 0 in Python, Sunday 0 in cron
    day_ok = (
        (at.day in dom or weekday in dow)
        if fields[2] not in ("*", "?") and fields[4] not in ("*", "?")
        else at.day in dom and weekday in dow
    )
    return at.minute in minute and at.hour in hour and at.month in month and day_ok
