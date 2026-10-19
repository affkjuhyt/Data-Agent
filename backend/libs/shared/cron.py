"""Shared 5-field cron due-check, promoted out of
`workflow_runtime/application/runtime_services/roi_use_cases.py`'s private `_cron_due` so
`platform_workers`' new `web-crawler` worker can use the same logic instead
of a second hand-rolled copy — both services already resolve `libs.shared.*`
(see e.g. `platform_workers/workers/schema_crawler/worker.py`'s
`from libs.shared.observability import setup_observability`)."""
from __future__ import annotations

import datetime as dt

from croniter import croniter


def cron_due(cron: str, base: dt.datetime, now: dt.datetime) -> bool:
    """True if `cron`'s next occurrence after `base` is <= `now`; never for an invalid schedule.

    croniter is the only parser: a hand-written fallback that ran on any croniter error ANDed
    day-of-month with day-of-week and stopped at the first item of a list."""
    nxt = next_run_after(cron, base)
    return nxt is not None and nxt <= now


def next_run_after(cron: str, base: dt.datetime) -> dt.datetime | None:
    """First occurrence of `cron` after `base`; None for an empty or invalid
    schedule (a manual-only source has no next run)."""
    if not cron or not cron.strip():
        return None
    try:
        return croniter(cron, base).get_next(dt.datetime)  # type: ignore[no-any-return]
    except ValueError:  # every croniter error (bad expression, impossible date) is one
        return None
