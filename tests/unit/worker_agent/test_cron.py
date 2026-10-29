"""The cron reading the api validates, previews and the scheduler fires on (libs/cron)."""

from __future__ import annotations

import datetime
import zoneinfo

import pytest

from libs import cron

pytestmark = [pytest.mark.unit]

AT = datetime.datetime(2026, 10, 19, 6, 30, tzinfo=datetime.UTC)  # a Monday
SAIGON = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("expr", "fires"),
    [
        ("* * * * *", True),
        ("30 6 * * *", True),
        ("31 6 * * *", False),
        ("*/15 * * * *", True),
        ("0,30 6-8 * * 1-5", True),
        ("30 6 * * sat,sun", False),
        ("30 6 19 10 *", True),
        ("30 6 1 * 1", True),  # either day field matches when both are restricted
        ("30 6 1 * 2", False),
        ("30 6 * * 7", False),
    ],
)
def test_matches(expr: str, fires: bool) -> None:
    assert cron.matches(expr, AT) is fires


def test_sunday_is_0_and_7() -> None:
    sunday = AT + datetime.timedelta(days=6)
    assert cron.matches("30 6 * * 0", sunday) and cron.matches("30 6 * * 7", sunday)


@pytest.mark.parametrize(
    "expr",
    [
        "7am",
        "* * * *",
        "60 * * * *",
        "* 24 * * *",
        "5-1 * * * *",
        "*/0 * * * *",
        # croniter reads a sixth field as seconds; nothing here fires on seconds.
        "0 0 * * * 30",
        "H * * * *",
    ],
)
def test_a_malformed_expression_is_refused(expr: str) -> None:
    with pytest.raises(cron.CronError):
        cron.check(expr)
    with pytest.raises(ValueError):
        cron.matches(expr, AT)


def test_check_collapses_whitespace_and_reads_macros() -> None:
    assert cron.check("  0  7 * *   1-5 ") == "0 7 * * 1-5"
    assert cron.check("@daily") == "@daily"


def test_runs_are_strictly_after_and_read_in_the_zone() -> None:
    # 06:30 UTC is 13:30 in Saigon: today's 13:30 has passed, tomorrow's is next.
    assert cron.next_runs("30 13 * * *", AT, 2, SAIGON) == [
        datetime.datetime(2026, 10, 20, 13, 30, tzinfo=SAIGON),
        datetime.datetime(2026, 10, 21, 13, 30, tzinfo=SAIGON),
    ]
    assert cron.next_runs("*/15 * * * *", AT, 2) == [
        datetime.datetime(2026, 10, 19, 6, 45, tzinfo=datetime.UTC),
        datetime.datetime(2026, 10, 19, 7, 0, tzinfo=datetime.UTC),
    ]


def test_a_skipped_day_is_asked_once_and_dropped() -> None:
    asked: list[datetime.date] = []

    def weekend(day: datetime.date) -> bool:
        asked.append(day)
        return day.isoweekday() >= 6

    friday = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.UTC)
    runs = cron.next_runs("*/30 * * * *", friday.replace(hour=23, minute=40), 1, skip_day=weekend)
    assert runs == [datetime.datetime(2026, 10, 26, 0, 0, tzinfo=datetime.UTC)]
    # Saturday and Sunday each asked once, not once per half hour.
    assert asked == [datetime.date(2026, 10, 24), datetime.date(2026, 10, 25), runs[0].date()]


def test_a_dst_gap_time_is_skipped() -> None:
    # 02:30 does not exist in New York on 8 March 2026.
    runs = cron.next_runs(
        "30 2 * * *", datetime.datetime(2026, 3, 7, 12, tzinfo=NEW_YORK), 2, NEW_YORK
    )
    assert [r.date().isoformat() for r in runs] == ["2026-03-09", "2026-03-10"]
    assert all(r.utcoffset() == datetime.timedelta(hours=-4) for r in runs)


def test_a_schedule_that_never_fires_has_no_runs() -> None:
    assert cron.next_runs("0 0 30 2 *", AT, 3) == []


def test_fires_per_day() -> None:
    assert cron.fires_per_day("0 7 * * 1-5") == 1
    assert cron.fires_per_day("*/15 * * * *") == 96
    assert cron.fires_per_day("0 * * * *") == 24
    assert cron.fires_per_day("@hourly") == 24
