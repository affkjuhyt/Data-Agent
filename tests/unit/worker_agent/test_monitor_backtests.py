"""A monitor's rule judged on its metric's past periods (UC-08-01 step 3, BR-32)."""

from __future__ import annotations

import datetime
import decimal

import pytest

from services.worker_agent.monitor_backtests import (
    Unjudgeable,
    Unsupported,
    complete_before,
    grain_of,
    judge,
)

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


def whole(**values: float | None) -> dict[str, dict[str, decimal.Decimal | None]]:
    """A series of the whole metric (no split): {"2026-09": {"": 3}}."""
    return {
        k.removeprefix("m").replace("_", "-"): {"": None if v is None else D(str(v))}
        for k, v in values.items()
    }


ABOVE_2 = {"direction": "above", "value": 2, "early": 1.9}


def test_a_threshold_fires_on_the_periods_past_it_and_not_on_the_early_band() -> None:
    series = whole(m2026_06=1.95, m2026_07=1.5, m2026_08=2.5, m2026_09=3)
    assert judge("threshold", ABOVE_2, series, periods=12, before="2026-10") == {
        "periods": 4,
        "fired": 2,
        "fired_at": ["2026-08", "2026-09"],
    }


def test_the_period_still_running_is_not_judged() -> None:
    series = whole(m2026_09=1, m2026_10=9)
    assert judge("threshold", ABOVE_2, series, periods=12, before="2026-10")["fired"] == 0


def test_only_the_last_periods_asked_for_are_judged() -> None:
    series = {f"2025-{m:02d}": {"": D(5)} for m in range(1, 13)}
    series |= {"2026-01": {"": D(1)}, "2026-02": {"": D(5)}}
    out = judge("threshold", ABOVE_2, series, periods=12, before="2026-03")
    # 2025-03 … 2026-02: eleven over the threshold, January 2026 under it.
    assert (out["periods"], out["fired"]) == (12, 11)
    assert out["fired_at"][0] == "2025-03" and "2026-01" not in out["fired_at"]


def test_a_period_without_a_figure_is_not_counted_as_quiet() -> None:
    series = whole(m2026_07=None, m2026_08=3)
    assert judge("threshold", ABOVE_2, series, periods=12, before="2026-10") == {
        "periods": 1,
        "fired": 1,
        "fired_at": ["2026-08"],
    }


def test_a_split_monitor_fires_in_a_period_when_any_member_does() -> None:
    series = {
        "2026-08": {"Miền Bắc": D("1"), "Miền Nam": D("2.5")},
        "2026-09": {"Miền Bắc": D("1"), "Miền Nam": None},
    }
    assert judge("threshold", ABOVE_2, series, periods=12, before="2026-10") == {
        "periods": 2,
        "fired": 1,
        "fired_at": ["2026-08"],
    }


def test_below_fires_under_the_value() -> None:
    rule = {"direction": "below", "value": 2}
    series = whole(m2026_08=1, m2026_09=3)
    assert judge("threshold", rule, series, periods=12, before="2026-10")["fired_at"] == ["2026-08"]


def test_a_change_fires_when_it_moves_more_than_the_percent_the_way_asked() -> None:
    rule = {"direction": "down", "value": 5, "base": "month"}
    # 100 → 94 is -6% (fires), 94 → 90 is -4.3%, 90 → 99 is +10% (the wrong way).
    series = whole(m2026_06=100, m2026_07=94, m2026_08=90, m2026_09=99)
    assert judge("change_vs_previous", rule, series, periods=12, before="2026-10") == {
        "periods": 3,
        "fired": 1,
        "fired_at": ["2026-07"],
    }
    up = {**rule, "direction": "up"}
    assert judge("change_vs_previous", up, series, periods=12, before="2026-10")["fired_at"] == [
        "2026-09"
    ]


def test_a_change_from_zero_or_nothing_is_not_judged() -> None:
    rule = {"direction": "up", "value": 5}
    series = whole(m2026_07=0, m2026_08=10, m2026_09=None)
    assert judge("change_vs_previous", rule, series, periods=12, before="2026-10") == {
        "periods": 0,
        "fired": 0,
        "fired_at": [],
    }


def test_a_change_is_judged_member_by_member() -> None:
    rule = {"direction": "up", "value": 50}
    series = {
        "2026-08": {"Bắc": D(10), "Nam": D(10)},
        "2026-09": {"Bắc": D(11), "Nam": D(20)},
    }
    assert judge("change_vs_previous", rule, series, periods=12, before="2026-10")["fired_at"] == [
        "2026-09"
    ]


def test_a_seasonal_anomaly_is_not_backtested() -> None:
    with pytest.raises(Unsupported):
        judge("seasonal_anomaly", {"sensitivity": "medium"}, whole(), periods=12, before="2026-10")


@pytest.mark.parametrize(
    ("kind", "rule"),
    [
        ("threshold", {"direction": "above"}),
        ("threshold", {"direction": "sideways", "value": 2}),
        ("change_vs_previous", {"direction": "up", "value": "lots"}),
        ("change_vs_previous", {"direction": "up", "value": -5}),
    ],
)
def test_a_rule_without_a_usable_level_is_refused(kind: str, rule: dict[str, object]) -> None:
    with pytest.raises(Unjudgeable):
        judge(kind, rule, whole(m2026_09=1), periods=12, before="2026-10")


def test_a_period_is_the_change_base_or_a_month_or_the_metric_coarser_grain() -> None:
    assert grain_of("change_vs_previous", {"base": "week"}, "month") == "week"
    assert grain_of("change_vs_previous", {}, "quarter") == "quarter"
    assert grain_of("threshold", {}, "quarter") == "quarter"
    assert grain_of("threshold", {}, None) == "month"
    # time_grain is the finest grain the metric reads at ('day' by default), not its period.
    assert grain_of("threshold", {}, "day") == "month"
    assert grain_of("threshold", {}, "week") == "month"
    # A fiscal grain labels periods "FY…": judged by calendar month instead.
    assert grain_of("threshold", {}, "fiscal_quarter") == "month"


@pytest.mark.parametrize(
    ("grain", "label"),
    [
        ("day", "2026-11-11"),
        ("week", "2026-11-05"),
        ("month", "2026-11"),
        ("quarter", "2026-Q4"),
        ("year", "2026"),
    ],
)
def test_the_first_label_of_a_period_not_yet_complete(grain: str, label: str) -> None:
    assert complete_before(datetime.date(2026, 11, 11), grain) == label
