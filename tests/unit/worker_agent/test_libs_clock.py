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
