"""Shared 5-field cron due-check, promoted out of
`workflow_runtime/application/runtime_services/roi_use_cases.py`'s private `_cron_due` so
`platform_workers`' new `web-crawler` worker can use the same logic instead
of a second hand-rolled copy — both services already resolve `libs.shared.*`
(see e.g. `platform_workers/workers/schema_crawler/worker.py`'s
`from libs.shared.observability import setup_observability`)."""
from __future__ import annotations

import datetime as dt


def _field_match(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/")
            step_n = int(step)
            lo = 0 if base == "*" else int(base)
            if value >= lo and (value - lo) % step_n == 0:
                return True
        elif "-" in part:
            lo, hi = (int(x) for x in part.split("-"))
            if lo <= value <= hi:
                return True
        elif int(part) == value:
            return True
    return False


def cron_due(cron: str, base: dt.datetime, now: dt.datetime) -> bool:
    """True if `cron`'s next occurrence at/after `base` is <= `now`."""
    try:
        from croniter import croniter
        return bool(croniter(cron, base).get_next(dt.datetime) <= now)
    except Exception:
        parts = cron.split()
        if len(parts) != 5:
            return False
        cursor = base.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = min(now, base + dt.timedelta(days=32))
        while cursor <= limit:
            if (
                _field_match(parts[0], cursor.minute)
                and _field_match(parts[1], cursor.hour)
                and _field_match(parts[2], cursor.day)
                and _field_match(parts[3], cursor.month)
                and _field_match(parts[4], (cursor.weekday() + 1) % 7)
            ):
                return cursor <= now
            cursor += dt.timedelta(minutes=1)
        return False


def next_run_after(cron: str, base: dt.datetime) -> dt.datetime | None:
    """First occurrence of `cron` after `base`; None for an empty or invalid
    schedule (a manual-only source has no next run)."""
    if not cron or not cron.strip():
        return None
    try:
        from croniter import croniter
        return croniter(cron, base).get_next(dt.datetime)  # type: ignore[no-any-return]
    except Exception:
        return None
