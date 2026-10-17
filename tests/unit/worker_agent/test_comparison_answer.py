"""compare_to (R1-06): the comparison a question asks for, and the compared answer's figures."""

from __future__ import annotations

import datetime
import decimal

import pytest

from services.worker_agent.answer.comparison import (
    comparison_figures,
    comparison_insights,
    period_label,
)
from services.worker_agent.answer.insights import evaluate, written
from services.worker_agent.semantic.compiler import Period
from services.worker_agent.understand.comparison import find_comparison

pytestmark = [pytest.mark.unit]

D = datetime.date


@pytest.mark.parametrize(
    ("question", "compare_to", "rest"),
    [
        (
            "Nợ xấu tháng 9/2026 so với tháng trước?",
            "previous_period",
            "Nợ xấu tháng 9/2026",
        ),
        (
            "Dư nợ quý 3 so với cùng kỳ năm trước",
            "same_period_last_year",
            "Dư nợ quý 3",
        ),
        ("Doanh thu năm 2026 so với năm ngoái", "previous_period", "Doanh thu năm 2026"),
        ("Doanh thu tháng 9 YoY", "same_period_last_year", "Doanh thu tháng 9"),
        ("Dư nợ tháng 9 so cùng kỳ", "same_period_last_year", "Dư nợ tháng 9"),
    ],
)
def test_the_comparison_is_read_and_taken_out(question: str, compare_to: str, rest: str) -> None:
    found = find_comparison(question)
    assert found is not None
    assert (found.compare_to, found.rest) == (compare_to, rest)


@pytest.mark.parametrize(
    "question",
    [
        "Nợ xấu tháng trước",  # the period itself, nothing compared
        "Số dư tháng trước theo vùng",
        "So sánh nợ xấu các vùng",
    ],
)
def test_no_comparison_is_read_where_none_is_asked(question: str) -> None:
    assert find_comparison(question) is None


@pytest.mark.parametrize(
    ("period", "label"),
    [
        (Period(D(2026, 8, 1), D(2026, 9, 1)), "tháng 8/2026"),
        (Period(D(2025, 7, 1), D(2025, 10, 1)), "quý 3/2025"),
        (Period(D(2025, 1, 1), D(2026, 1, 1)), "năm 2025"),
        (Period(D(2026, 10, 5), D(2026, 10, 6)), "05/10/2026"),
    ],
)
def test_the_base_period_is_named(period: Period, label: str) -> None:
    assert period_label(period) == label


def compared(unit: str | None = "tỷ đồng") -> list:  # type: ignore[type-arg]
    return comparison_figures(
        ["vung", "value", "base_value"],
        [["Miền Bắc", 2000, 500], ["Miền Nam", 1000, None], ["Miền Trung", 300, 400]],
        metric_name="Dư nợ",
        unit=unit,
        decimals=0,
        dimension_names={"vung": "Vùng"},
        period_label="tháng 9/2026",
        base_label="tháng 8/2026",
    )


def test_two_traced_figures_a_row_told_apart_by_period() -> None:
    figures = compared()
    assert [(f.key, f.row, f.column, f.dimensions["ky"]) for f in figures[:2]] == [
        ("F1", 0, "value", "tháng 9/2026"),
        ("F2", 0, "base_value", "tháng 8/2026"),
    ]
    assert figures[3].value is None and figures[3].display_state == "not_available"
    assert figures[1].label == "Dư nợ, Vùng Miền Bắc, tháng 8/2026"


def test_the_change_of_each_row_is_derived_and_read() -> None:
    figures = compared()
    ins = comparison_insights(
        figures,
        group_by=["vung"],
        metric_name="Dư nợ",
        unit="tỷ đồng",
        decimals=0,
        period_label="tháng 9/2026",
        base_label="tháng 8/2026",
    )
    exprs = [(f.expr, f.value) for f in ins.figures]
    # The South has no August figure: no change is made up for it.
    assert exprs == [
        ("F1 - F2", decimal.Decimal(1500)),
        ("(F1 - F2) / F2 * 100", decimal.Decimal(300)),
        ("F5 - F6", decimal.Decimal(-100)),
        ("(F5 - F6) / F6 * 100", decimal.Decimal(-25)),
    ]
    values = {f.key: f.value for f in figures if f.value is not None}
    assert all(evaluate(f.expr or "", values) == f.value for f in ins.figures)
    text = written(ins.sentences, figures + ins.figures)
    assert "1 nhóm tăng, 1 nhóm giảm" in text
    assert "Tăng mạnh nhất là Miền Bắc: +1.500 tỷ đồng (+300,0%)" in text
    assert "Giảm mạnh nhất là Miền Trung: -100 tỷ đồng (-25,0%)" in text


def test_a_percentage_changes_in_points() -> None:
    figures = comparison_figures(
        ["value", "base_value"],
        [[decimal.Decimal("1.83"), decimal.Decimal("1.49")]],
        metric_name="Tỷ lệ nợ xấu",
        unit="%",
        decimals=2,
        dimension_names={},
        period_label="tháng 9/2026",
        base_label="tháng 8/2026",
    )
    ins = comparison_insights(
        figures,
        group_by=[],
        metric_name="Tỷ lệ nợ xấu",
        unit="%",
        decimals=2,
        period_label="tháng 9/2026",
        base_label="tháng 8/2026",
    )
    assert [(f.unit, f.value) for f in ins.figures] == [("điểm %", decimal.Decimal("0.34"))]
    assert written(ins.sentences, figures + ins.figures) == (
        "Tháng 9/2026 tăng +0,34 điểm % so với tháng 8/2026."
    )


def test_a_masked_side_derives_nothing() -> None:
    figures = compared()
    figures[1].display_state, figures[1].value = "masked_stale", None
    ins = comparison_insights(
        figures,
        group_by=["vung"],
        metric_name="Dư nợ",
        unit="tỷ đồng",
        decimals=0,
        period_label="tháng 9/2026",
        base_label="tháng 8/2026",
    )
    assert all("F1" not in (f.expr or "") and "F2" not in (f.expr or "") for f in ins.figures)
