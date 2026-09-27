"""Unit tests for backend/libs/indicator/threshold.py (T4 in docs/archive/v1-ankt/TECH-DESIGN.md).

The trap under test is TECH-DESIGN R3: a 2025 figure judged against the
2026-2030 resolution. It is graded above rounding errors in the risk list
because it invalidates the conclusion rather than the last digit — "exceeded
the ceiling" is either true or it destroys trust in everything else.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.indicator.period import Period
from libs.indicator.quantity import Quantity, Unit, UnitError
from libs.indicator.threshold import (
    Assessment,
    Level,
    Status,
    Threshold,
    applicable_thresholds,
    assess,
)

pytestmark = pytest.mark.unit


def pct(value: str) -> Quantity:
    return Quantity(Decimal(value), Unit.PERCENT)


# The two resolutions the golden use case spans.
NQ23_CEILING = Threshold(Level.LIMIT, "<=", pct("60"), 2021, 2025, "NQ 23/2021/QH15")
NQ23_WARNING = Threshold(Level.WARNING, "<=", pct("55"), 2021, 2025, "NQ 23/2021/QH15")
NQ26_CEILING = Threshold(Level.LIMIT, "<=", pct("60"), 2026, 2030, "NQ 26/2026/QH16")

ALL = (NQ23_CEILING, NQ23_WARNING, NQ26_CEILING)


# ── selection is by the data's period ─────────────────────────────────────────

def test_a_2025_figure_is_judged_by_the_2021_resolution():
    applicable = applicable_thresholds(ALL, Period.parse("2025"))

    assert {t.legal_basis_label for t in applicable} == {"NQ 23/2021/QH15"}


def test_a_2026_figure_is_judged_by_the_2026_resolution():
    applicable = applicable_thresholds(ALL, Period.parse("2026"))

    assert {t.legal_basis_label for t in applicable} == {"NQ 26/2026/QH16"}


@pytest.mark.parametrize(("year", "expected"), [
    (2020, set()),                      # before the first resolution
    (2021, {"NQ 23/2021/QH15"}),        # first year covered
    (2025, {"NQ 23/2021/QH15"}),        # last year covered — the boundary
    (2026, {"NQ 26/2026/QH16"}),        # first year of the successor
    (2030, {"NQ 26/2026/QH16"}),
    (2031, set()),                      # after the last one
])
def test_the_boundaries_of_a_validity_range_are_inclusive(year, expected):
    """A resolution written "2021-2025" governs the whole of 2025."""
    applicable = applicable_thresholds(ALL, Period.parse(str(year)))

    assert {t.legal_basis_label for t in applicable} == expected


def test_an_open_ended_threshold_covers_every_later_year():
    current = Threshold(Level.LIMIT, "<=", pct("60"), 2026, None, "current")

    assert current.covers(Period.parse("2050"))
    assert not current.covers(Period.parse("2025"))


def test_a_quarterly_period_is_judged_by_its_year():
    assert NQ23_CEILING.covers(Period.parse("2025-Q4"))
    assert not NQ23_CEILING.covers(Period.parse("2026-Q1"))


# ── status ────────────────────────────────────────────────────────────────────

def test_a_figure_under_every_threshold_is_ok():
    result = assess(pct("34.7"), ALL, Period.parse("2024"))

    assert result.status is Status.OK
    assert result.breached == ()


def test_crossing_the_warning_but_not_the_limit_warns():
    result = assess(pct("57.0"), ALL, Period.parse("2024"))

    assert result.status is Status.WARNING
    assert [t.level for t in result.breached] == [Level.WARNING]


def test_crossing_the_limit_is_a_breach():
    result = assess(pct("62.0"), ALL, Period.parse("2024"))

    assert result.status is Status.BREACH
    assert {t.level for t in result.breached} == {Level.LIMIT, Level.WARNING}


def test_exactly_on_the_limit_is_not_a_breach():
    """`<=` means 60,0% is still within the ceiling — the boundary an eval case
    has to pin, since it is where rounding meets the rule. It is a warning, not
    a breach, because 60,0 is also past the 55 warning level.
    """
    on_the_limit = assess(pct("60.0"), ALL, Period.parse("2024"))
    just_over = assess(pct("60.1"), ALL, Period.parse("2024"))

    assert on_the_limit.status is Status.WARNING
    assert [t.level for t in on_the_limit.breached] == [Level.WARNING]
    assert just_over.status is Status.BREACH


def test_exactly_on_a_lone_limit_is_ok():
    assert assess(pct("60.0"), (NQ23_CEILING,), Period.parse("2024")).status is Status.OK


def test_a_period_with_no_rule_is_not_reported_as_ok():
    """"No ceiling was defined for 2019" and "2019 was within the ceiling" are
    different statements, and only one of them is reassuring."""
    result = assess(pct("34.7"), ALL, Period.parse("2019"))

    assert result.status is Status.NO_THRESHOLD
    assert result.breached == ()


def test_a_floor_is_expressed_by_the_operator_not_by_the_level():
    floor = Threshold(Level.LIMIT, ">=", pct("20"), 2021, 2030, "coverage floor")

    assert assess(pct("18.0"), (floor,), Period.parse("2024")).status is Status.BREACH
    assert assess(pct("25.0"), (floor,), Period.parse("2024")).status is Status.OK


# ── citation ──────────────────────────────────────────────────────────────────

def test_the_basis_cited_is_the_rule_that_was_broken():
    result = assess(pct("62.0"), ALL, Period.parse("2024"))

    assert result.legal_basis == "NQ 23/2021/QH15"


def test_a_compliant_figure_still_cites_the_rule_it_cleared():
    result = assess(pct("34.7"), ALL, Period.parse("2024"))

    assert result.legal_basis == "NQ 23/2021/QH15"


def test_no_rule_means_nothing_to_cite():
    assert Assessment(Status.NO_THRESHOLD).legal_basis is None


# ── malformed thresholds ──────────────────────────────────────────────────────

def test_an_unknown_operator_is_refused():
    with pytest.raises(ValueError, match="unknown operator"):
        Threshold(Level.LIMIT, "=~", pct("60"), 2021, 2025)


def test_a_range_that_ends_before_it_starts_is_refused():
    with pytest.raises(ValueError, match="cannot stop applying before it starts"):
        Threshold(Level.LIMIT, "<=", pct("60"), 2025, 2021)


def test_comparing_against_a_different_unit_raises():
    money = Quantity(Decimal("3109.2"), Unit.MONEY, 12, "VND")

    with pytest.raises(UnitError, match="cannot compare"):
        assess(money, ALL, Period.parse("2024"))
