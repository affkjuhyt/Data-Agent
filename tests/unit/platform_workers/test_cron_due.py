"""cron_due used to fall back to a hand-written matcher whenever croniter raised (or was not
installed). That matcher ANDed day-of-month with day-of-week (cron ORs them) and stopped at
the first item of a list ("1,5"). croniter is the only parser now; these pin its behaviour."""

from __future__ import annotations

import datetime as dt

import pytest

from libs.shared.cron import cron_due, next_run_after

pytestmark = [pytest.mark.unit]

BASE = dt.datetime(2026, 10, 1, 0, 0)  # a Thursday


def test_due_after_next_occurrence() -> None:
    assert cron_due("0 6 * * *", BASE, dt.datetime(2026, 10, 1, 6, 0))
    assert not cron_due("0 6 * * *", BASE, dt.datetime(2026, 10, 1, 5, 59))


def test_list_items_after_the_first_match() -> None:
    assert cron_due("0 1,5 * * *", dt.datetime(2026, 10, 1, 2, 0), dt.datetime(2026, 10, 1, 5, 0))


def test_day_of_month_or_day_of_week() -> None:
    # "the 15th, or any Monday": Monday 5 Oct is due although it is not the 15th.
    assert cron_due("0 0 15 * 1", BASE, dt.datetime(2026, 10, 5, 0, 0))


@pytest.mark.parametrize("cron", ["99 99 * * *", "not a cron", ""])
def test_invalid_expression_is_never_due(cron: str) -> None:
    assert not cron_due(cron, BASE, BASE + dt.timedelta(days=400))
    assert next_run_after(cron, BASE) is None
