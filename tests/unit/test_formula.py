"""Unit tests for backend/libs/indicator/formula.py (T3 in docs/archive/v1-ankt/TECH-DESIGN.md).

Two things carry the weight here. The first is that compiling happens without
data, so an unusable formula is rejected when it is authored. The second is the
stock/flow trap of TECH-DESIGN R2: end-of-period debt over a period's GDP is
the standard ratio for a year and nonsense for a quarter, and the arithmetic
never complains.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from libs.indicator.formula import (
    Alignment,
    AsPercent,
    Difference,
    FormulaError,
    Ratio,
    ResultType,
    StockOrFlow,
    Sum,
    Var,
    VariableSpec,
    compile_formula,
    decompose_change,
    evaluate,
    variables,
)
from libs.indicator.period import Frequency, Period
from libs.indicator.quantity import Quantity, Unit

pytestmark = pytest.mark.unit


DEBT = VariableSpec("debt", Unit.MONEY, StockOrFlow.STOCK, 12, "VND")
GDP = VariableSpec("gdp", Unit.MONEY, StockOrFlow.FLOW, 12, "VND")
REVENUE = VariableSpec("revenue", Unit.MONEY, StockOrFlow.FLOW, 12, "VND")
HEADCOUNT = VariableSpec("headcount", Unit.PERSONS, StockOrFlow.STOCK)

SPECS = {s.variable_id: s for s in (DEBT, GDP, REVENUE, HEADCOUNT)}

# Illustrative and self-consistent, not quoted from a bulletin.
OBSERVED = {
    ("debt", "2024"): Quantity(Decimal("3109.2"), Unit.MONEY, 12, "VND"),
    ("gdp", "2024"): Quantity(Decimal("8960.2"), Unit.MONEY, 12, "VND"),
    ("debt", "2023"): Quantity(Decimal("3000.0"), Unit.MONEY, 12, "VND"),
    ("gdp", "2023"): Quantity(Decimal("8310.8"), Unit.MONEY, 12, "VND"),
    ("revenue", "2024"): Quantity(Decimal("1997.3"), Unit.MONEY, 12, "VND"),
}


def resolve(variable_id: str, period: Period) -> Quantity:
    return OBSERVED[(variable_id, str(period))]


DEBT_TO_GDP = AsPercent(Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR))


# ── structure ─────────────────────────────────────────────────────────────────

def test_variables_are_collected_from_the_whole_tree():
    assert variables(DEBT_TO_GDP) == {"debt", "gdp"}


def test_undeclared_variable_is_rejected_before_any_data_is_fetched():
    with pytest.raises(FormulaError, match="undeclared variables: \\['missing'\\]"):
        compile_formula(Ratio(Var("debt"), Var("missing")), SPECS)


def test_an_empty_sum_is_not_a_formula():
    with pytest.raises(FormulaError, match="at least one component"):
        compile_formula(Sum(()), SPECS)


# ── unit typing at compile time ───────────────────────────────────────────────

def test_ratio_of_unlike_units_is_rejected():
    with pytest.raises(FormulaError, match="cannot form a ratio of money over persons"):
        compile_formula(Ratio(Var("debt"), Var("headcount")), SPECS)


def test_sum_of_unlike_units_is_rejected():
    with pytest.raises(FormulaError, match="cannot sum"):
        compile_formula(Sum((Var("debt"), Var("headcount"))), SPECS)


def test_only_a_ratio_becomes_a_percent():
    with pytest.raises(FormulaError, match="only a ratio"):
        compile_formula(AsPercent(Var("debt")), SPECS)


def test_a_difference_of_percents_types_as_percentage_points():
    formula = Difference(DEBT_TO_GDP, DEBT_TO_GDP)

    assert compile_formula(formula, SPECS).unit is Unit.PERCENTAGE_POINT


def test_compiled_type_reports_unit_and_kind():
    assert compile_formula(DEBT_TO_GDP, SPECS) == ResultType(
        Unit.PERCENT, StockOrFlow.RATIO, 0, None, Frequency.ANNUAL,
    )


# ── the stock/flow trap (R2) ──────────────────────────────────────────────────

def test_a_stock_over_a_flow_must_declare_its_alignment():
    with pytest.raises(FormulaError, match="must name its alignment rule"):
        compile_formula(Ratio(Var("debt"), Var("gdp")), SPECS)


def test_two_flows_need_no_alignment():
    compile_formula(Ratio(Var("revenue"), Var("gdp")), SPECS)


def test_alignment_on_a_ratio_that_does_not_mix_is_rejected():
    """Declaring a rule where none applies hides that nobody checked."""
    with pytest.raises(FormulaError, match="only meaningful where a stock meets a flow"):
        compile_formula(
            Ratio(Var("revenue"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR), SPECS)


def test_an_annual_alignment_refuses_a_quarterly_period():
    """The R2 failure itself: the same formula, asked for a quarter, would
    divide an end-of-quarter stock by one quarter's GDP and read about four
    times too low — with nothing in the arithmetic to object."""
    with pytest.raises(FormulaError, match="aligns on A periods but was asked for 2024-Q2"):
        evaluate(DEBT_TO_GDP, SPECS, resolve, Period.parse("2024-Q2"))


def test_the_same_formula_evaluates_for_a_year():
    result = evaluate(DEBT_TO_GDP, SPECS, resolve, Period.parse("2024"))

    assert result.unit is Unit.PERCENT


# ── evaluation ────────────────────────────────────────────────────────────────

def test_debt_to_gdp_end_to_end():
    result = evaluate(DEBT_TO_GDP, SPECS, resolve, Period.parse("2024")).round_to(1)

    assert (result.value, result.unit) == (Decimal("34.7"), Unit.PERCENT)


def test_sum_adds_components():
    total = evaluate(Sum((Var("gdp"), Var("revenue"))), SPECS, resolve, Period.parse("2024"))

    assert total.value == Decimal("10957.5")


def test_evaluation_is_not_rounded_until_asked():
    result = evaluate(DEBT_TO_GDP, SPECS, resolve, Period.parse("2024"))

    assert result.value != Decimal("34.7")
    assert result.round_to(1).value == Decimal("34.7")


# ── decomposition ─────────────────────────────────────────────────────────────

def test_decomposition_parts_sum_to_the_total():
    ratio = Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR)

    result = decompose_change(ratio, SPECS, resolve, Period.parse("2024"))

    result.check()  # redundant with decompose_change, asserted here on purpose
    assert result.total.unit is Unit.PERCENTAGE_POINT
    assert result.numerator_effect.unit is Unit.PERCENTAGE_POINT
    assert result.denominator_effect.unit is Unit.PERCENTAGE_POINT


def test_decomposition_attributes_a_fall_to_the_denominator():
    """Debt rose 3,6% while GDP rose 7,8%, so the ratio fell — and the reason
    is the denominator, which is the answer the golden use case asks for about
    2017-2020."""
    ratio = Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR)

    result = decompose_change(ratio, SPECS, resolve, Period.parse("2024"))

    assert result.total.value < 0
    assert result.numerator_effect.value > 0
    assert result.denominator_effect.value < 0
    assert abs(result.denominator_effect.value) > abs(result.numerator_effect.value)


def test_decomposition_compares_against_the_year_before_by_default():
    ratio = Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR)

    explicit = decompose_change(
        ratio, SPECS, resolve, Period.parse("2024"), base=Period.parse("2023"))
    implicit = decompose_change(ratio, SPECS, resolve, Period.parse("2024"))

    assert explicit.total.value == implicit.total.value


def test_decomposition_of_a_zero_denominator_raises():
    observed = dict(OBSERVED)
    observed[("gdp", "2023")] = Quantity(Decimal("0"), Unit.MONEY, 12, "VND")
    ratio = Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR)

    with pytest.raises(ZeroDivisionError):
        decompose_change(
            ratio, SPECS, lambda v, p: observed[(v, str(p))], Period.parse("2024"))
