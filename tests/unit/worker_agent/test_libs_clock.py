"""libs.clock: the day is the organisation's, not the server's (GEN-21)."""

from __future__ import annotations

import datetime

import pytest

from libs import clock

pytestmark = [pytest.mark.unit]


def test_today_is_the_zones_day_not_utcs() -> None:
    # 17:30 UTC on 31 October is already 1 November in Vietnam: "this month" is November there.
    now = datetime.datetime(2026, 10, 31, 17, 30, tzinfo=datetime.UTC)
    assert clock.today(clock.zone_named("Asia/Ho_Chi_Minh"), now) == datetime.date(2026, 11, 1)
    assert clock.today(clock.zone_named("America/New_York"), now) == datetime.date(2026, 10, 31)


@pytest.mark.parametrize("name", [None, "", "Mars/Olympus_Mons", "../etc/passwd"])
def test_a_zone_this_host_cannot_resolve_reads_as_utc(name: str | None) -> None:
    assert clock.zone_named(name) is clock.UTC


JULY_END = clock.Calendar(fiscal_year_start_month=7, fiscal_year_named_by="end")
APRIL_START = clock.Calendar(week_start=7, fiscal_year_start_month=4, fiscal_year_named_by="start")
D = datetime.date


def test_a_fiscal_year_is_named_by_the_year_it_ends_or_starts_in() -> None:
    assert clock.fiscal_year_of(D(2025, 7, 1), JULY_END) == 2026
    assert clock.fiscal_year_of(D(2025, 6, 30), JULY_END) == 2025
    assert clock.fiscal_year(2026, JULY_END) == (D(2025, 7, 1), D(2026, 7, 1))
    assert clock.fiscal_year_of(D(2026, 3, 31), APRIL_START) == 2025
    assert clock.fiscal_year(2025, APRIL_START) == (D(2025, 4, 1), D(2026, 4, 1))
    # The calendar year, whichever naming.
    assert clock.fiscal_year(2026) == (D(2026, 1, 1), D(2027, 1, 1))


def test_fiscal_quarters_count_from_the_fiscal_years_first_month() -> None:
    assert clock.fiscal_quarter_of(D(2025, 10, 5), JULY_END) == (2026, 2)
    assert clock.fiscal_quarter(2026, 2, JULY_END) == (D(2025, 10, 1), D(2026, 1, 1))
    assert clock.fiscal_quarter(2026, 4, JULY_END) == (D(2026, 4, 1), D(2026, 7, 1))


def test_a_week_starts_on_the_calendars_weekday() -> None:
    sunday = D(2026, 11, 1)
    assert clock.week_of(sunday) == (D(2026, 10, 26), D(2026, 11, 2))
    assert clock.week_of(sunday, APRIL_START) == (D(2026, 11, 1), D(2026, 11, 8))


def test_to_date_starts_at_the_fiscal_year() -> None:
    assert clock.start_of(D(2026, 3, 3), "year", JULY_END) == D(2025, 7, 1)
    assert clock.start_of(D(2026, 3, 3), "quarter", JULY_END) == D(2026, 1, 1)
    assert clock.start_of(D(2026, 3, 3), "year") == D(2026, 1, 1)
    assert clock.start_of(D(2026, 3, 3), "month", JULY_END) == D(2026, 3, 1)
