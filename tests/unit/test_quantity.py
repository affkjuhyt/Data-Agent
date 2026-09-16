"""Unit tests for backend/libs/indicator/quantity.py (T1 in docs/ankt/TECH-DESIGN.md).

The module exists to make four mistakes impossible rather than merely
discouraged, so the tests are organised around those four: float creeping in,
the multiplier being misread as a factor, percent/percentage-point collapsing
into one thing, and rounding happening more than once.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.indicator.quantity import Quantity, Unit, UnitError

pytestmark = pytest.mark.unit


def vnd(value: str, multiplier: int = 12) -> Quantity:
    return Quantity(Decimal(value), Unit.MONEY, multiplier, "VND")


def pct(value: str) -> Quantity:
    return Quantity(Decimal(value), Unit.PERCENT)


# ── Decimal, never float ──────────────────────────────────────────────────────

def test_float_is_refused_at_construction():
    with pytest.raises(TypeError, match="refuses float"):
        Quantity(0.1, Unit.RATIO)  # type: ignore[arg-type]


def test_strings_are_accepted_and_keep_their_digits():
    assert Quantity("0.1", Unit.RATIO).value == Decimal("0.1")


def test_a_sum_of_published_figures_is_exact():
    """float already has the wrong digits before any rounding is asked for."""
    # Guard the premise — if float ever gets this right the test is vacuous.
    assert 3109.2 + 0.1 == 3109.2999999999997

    assert (vnd("3109.2") + vnd("0.1")).value == Decimal("3109.3")


def test_addition_round_trips_where_float_would_not():
    """The property that motivates Decimal: a + b - b == a, exactly."""
    assert 0.1 + 0.2 - 0.2 != 0.1

    a, b = vnd("0.1"), vnd("0.2")
    assert (a + b - b).value == a.value


def test_scaling_refuses_float_too():
    with pytest.raises(TypeError):
        pct("34.7").scaled_by(1.5)  # type: ignore[arg-type]


# ── the multiplier is an exponent ─────────────────────────────────────────────

def test_value_stays_as_published():
    # 3.109,2 nghìn tỷ đồng is stored the way the bulletin prints it, not as
    # 3109200000000 — so it can be shown back unchanged.
    debt = vnd("3109.2")
    assert (debt.value, debt.multiplier) == (Decimal("3109.2"), 12)
    assert debt.base_value() == Decimal("3.1092E+15")


def test_rescale_is_exact_in_both_directions():
    debt = vnd("3109.2")
    assert debt.rescale(9).value == Decimal("3109200")
    assert debt.rescale(9).rescale(12).value == debt.value


def test_addition_aligns_different_scales():
    # 3.109,2 nghìn tỷ + 500 tỷ, written at the two scales the sources use.
    total = vnd("3109.2", 12) + vnd("500", 9)
    assert total.base_value() == vnd("3109.7", 12).base_value()


def test_alignment_never_shifts_digits_away():
    """Aligning to the FINER scale, so the smaller figure keeps its precision."""
    total = vnd("1", 12) + vnd("1.5", 9)
    assert total.multiplier == 9
    assert total.value == Decimal("1001.5")


# ── percent is not percentage point ───────────────────────────────────────────

def test_subtracting_percents_yields_percentage_points():
    change = pct("34.7") - pct("33.5")
    assert change.unit is Unit.PERCENTAGE_POINT
    assert change.value == Decimal("1.2")


def test_percentage_points_do_not_silently_become_percent_again():
    points = pct("34.7") - pct("33.5")
    with pytest.raises(UnitError, match="cannot add"):
        points + pct("1.0")


def test_money_and_percent_do_not_mix():
    with pytest.raises(UnitError, match="cannot add"):
        vnd("100") + pct("5")


def test_different_currencies_need_an_explicit_rate():
    usd = Quantity(Decimal("100"), Unit.MONEY, 9, "USD")
    with pytest.raises(UnitError, match="exchange rate"):
        vnd("100", 9) + usd


def test_money_must_name_a_currency():
    with pytest.raises(UnitError, match="must name its currency"):
        Quantity(Decimal("100"), Unit.MONEY, 12)


def test_a_percent_cannot_carry_a_currency():
    with pytest.raises(UnitError, match="cannot carry a currency"):
        Quantity(Decimal("34.7"), Unit.PERCENT, 0, "VND")


# ── ratios ────────────────────────────────────────────────────────────────────

def test_ratio_of_like_units_is_dimensionless():
    ratio = vnd("3109.2").ratio_to(vnd("8960.2"))
    assert ratio.unit is Unit.RATIO
    assert ratio.currency is None


def test_ratio_across_scales_is_correct():
    """The denominator written in tỷ, the numerator in nghìn tỷ."""
    ratio = vnd("1", 12).ratio_to(vnd("4000", 9))
    assert ratio.value == Decimal("0.25")


def test_ratio_refuses_unlike_units():
    people = Quantity(Decimal("100"), Unit.PERSONS)
    with pytest.raises(UnitError, match="cannot divide"):
        vnd("3109.2").ratio_to(people)


def test_ratio_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        vnd("3109.2").ratio_to(vnd("0"))


def test_only_a_ratio_becomes_a_percent():
    with pytest.raises(UnitError, match="only a ratio"):
        vnd("3109.2").as_percent()


# ── rounding happens once ─────────────────────────────────────────────────────

def test_rounds_half_up_like_the_source_does():
    assert pct("34.65").round_to(1).value == Decimal("34.7")
    assert pct("2.5").round_to(0).value == Decimal("3")


def test_rounding_keeps_trailing_zeros_of_the_published_precision():
    assert str(pct("34.0").round_to(1).value) == "34.0"


def test_intermediate_steps_are_not_rounded():
    """A ratio of two published figures rounds once, at the end — not at each
    step, which would compound the error the tolerance policy budgets for."""
    ratio = vnd("3109.2").ratio_to(vnd("8960.2")).as_percent()

    assert ratio.value > Decimal("34.699")
    assert ratio.round_to(1).value == Decimal("34.7")


# ── golden calculation, end to end ────────────────────────────────────────────

def test_public_debt_to_gdp_end_to_end():
    """Public debt / GDP, the golden use case, through every step.

    Figures are illustrative and self-consistent, not quoted from a bulletin —
    the real ones arrive with the source documents. What is pinned here is the
    mechanism: two money figures at different published scales become a ratio,
    a percent, and a percentage-point change, with one rounding at the end.
    """
    debt_2024 = vnd("3109.2", 12)
    gdp_2024 = vnd("8960200", 9)

    share = debt_2024.ratio_to(gdp_2024).as_percent().round_to(1)
    assert (share.value, share.unit) == (Decimal("34.7"), Unit.PERCENT)

    share_2023 = pct("36.1")
    change = share - share_2023
    assert (change.value, change.unit) == (Decimal("-1.4"), Unit.PERCENTAGE_POINT)
