"""libs.shared.cron.cron_due — promoted from workflow_runtime's private
_cron_due (roi_use_cases.py) so platform_workers' web-crawler worker can
share the same per-source due-check instead of a second hand-rolled copy."""
from __future__ import annotations

import datetime as dt

import pytest

from libs.shared.cron import cron_due

pytestmark = pytest.mark.unit


def test_daily_cron_is_due_after_24_hours():
    base = dt.datetime(2026, 1, 1, 3, 0, 0)
    not_yet = dt.datetime(2026, 1, 2, 2, 59, 0)
    due = dt.datetime(2026, 1, 2, 3, 0, 0)
    assert cron_due("0 3 * * *", base, not_yet) is False
    assert cron_due("0 3 * * *", base, due) is True


def test_hourly_cron_is_due_after_one_hour():
    base = dt.datetime(2026, 1, 1, 3, 0, 0)
    assert cron_due("0 * * * *", base, base + dt.timedelta(minutes=59)) is False
    assert cron_due("0 * * * *", base, base + dt.timedelta(hours=1)) is True


def test_malformed_cron_is_never_due():
    base = dt.datetime(2026, 1, 1, 0, 0, 0)
    now = base + dt.timedelta(days=10)
    assert cron_due("not a cron", base, now) is False


def test_far_future_now_is_due_for_a_daily_cron():
    base = dt.datetime(2026, 1, 1, 3, 0, 0)
    now = dt.datetime(2026, 3, 1, 0, 0, 0)
    assert cron_due("0 3 * * *", base, now) is True
