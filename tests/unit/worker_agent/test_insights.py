"""The analyst's reading of a result, by arithmetic: every number a figure with its expression."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.answer.insights import (
    ExprError,
    evaluate,
    insights_of,
    is_additive,
    written,
)
from services.worker_agent.answer.narrate import Figure

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


def _figs(code: str, members: list[tuple[str, str]], unit: str | None = "%") -> list[Figure]:
    return [
        Figure(f"F{i + 1}", f"NPL, {m}", D(v), unit, 2, i, "value", {code: m})
        for i, (m, v) in enumerate(members)
    ]


def _read(figures: list[Figure], code: str, *, additive: bool = False, unit: str | None = "%"):
    return insights_of(
        figures,
        group_by=[code],
        time_dimensions={"thang"},
        metric_name="Tỷ lệ nợ xấu",
        unit=unit,
        decimals=2,
        additive=additive,
    )


def test_evaluate_takes_arithmetic_over_figures_only() -> None:
    assert evaluate("(F2 - F1) / F1 * 100", {"F1": D(4), "F2": D(5)}) == D(25)
    for bad in ("__import__('os')", "F1 ** 2", "F9", "f(F1)"):
        with pytest.raises(ExprError):
            evaluate(bad, {"F1": D(1)})


def test_across_members_names_the_extremes_and_the_gap() -> None:
    figs = _figs("vung", [("Bắc", "1.84"), ("Trung", "2.31"), ("Nam", "1.52")])
    ins = _read(figs, "vung")
    [gap] = ins.figures
    assert (gap.key, gap.expr, gap.value, gap.unit) == ("F4", "F2 - F3", D("0.79"), "điểm %")
    assert written(ins.sentences, figs + ins.figures) == (
        "Cao nhất là Trung (2,31%), thấp nhất là Nam (1,52%), chênh 0,79 điểm %."
    )


def test_a_ratio_gets_no_total_but_an_additive_metric_gets_its_share() -> None:
    members = [("Bắc", "300"), ("Trung", "100"), ("Nam", "100")]
    assert len(_read(_figs("vung", members, unit="tỷ đồng"), "vung", unit="tỷ đồng").figures) == 1
    figs = _figs("vung", members, unit="tỷ đồng")
    ins = _read(figs, "vung", additive=True, unit="tỷ đồng")
    gap, total, share = ins.figures
    assert total.value == D(500) and total.expr == "F1 + F2 + F3"
    assert share.value == D(60) and share.unit == "%"
    assert "Tổng là 500,00 tỷ đồng; Bắc chiếm 60,0%." in written(ins.sentences, figs + ins.figures)


def test_over_time_the_last_period_against_the_one_before() -> None:
    figs = _figs("thang", [("2026-09", "120"), ("2026-07", "90"), ("2026-08", "100")], unit=None)
    ins = _read(figs, "thang", unit=None)
    delta, pct = ins.figures
    assert (delta.expr, delta.value, delta.signed) == ("F1 - F3", D(20), True)
    assert pct.value == D(20) and pct.expr == "(F1 - F3) / F3 * 100"
    text = written(ins.sentences, figs + ins.figures)
    assert text.startswith("2026-09 tăng so với 2026-08: +20,00 (+20,0%).")
    assert "cao nhất là 2026-09 (120,00), thấp nhất là 2026-07 (90,00)" in text


def test_a_percentage_changes_in_points_never_a_percent_of_a_percent() -> None:
    figs = _figs("thang", [("2026-08", "2.00"), ("2026-09", "1.50")])
    ins = _read(figs, "thang")
    [delta] = ins.figures
    assert delta.unit == "điểm %" and delta.value == D("-0.50")
    assert (
        written(ins.sentences, figs + ins.figures) == "2026-09 giảm so với 2026-08: -0,50 điểm %."
    )


def test_a_previous_period_of_zero_has_no_rate_of_change() -> None:
    figs = _figs("thang", [("2026-08", "0"), ("2026-09", "5")], unit=None)
    assert [f.expr for f in _read(figs, "thang", unit=None).figures] == ["F2 - F1"]


def test_a_member_far_from_the_rest_is_said_to_stand_apart() -> None:
    members = list(zip("ABCDEF", ["10", "11", "10", "12", "11", "40"], strict=True))
    ins = _read(_figs("vung", members, unit=None), "vung", unit=None)
    assert ins.sentences[-1] == "F khác hẳn các nhóm còn lại."


def test_br178_nothing_is_derived_from_a_masked_figure() -> None:
    figs = _figs("vung", [("Bắc", "1"), ("Nam", "2")])
    figs[1].display_state, figs[1].value = "masked_stale", None
    ins = _read(figs, "vung")
    assert ins.figures == [] and ins.sentences == []


def test_additive_only_for_one_summed_or_counted_measure() -> None:
    measures = {
        "du_no": "SUM(balance)",
        "so_hd": "count(id)",
        "so_kh": "count(distinct id)",
        "lai": "AVG(rate)",
    }
    assert is_additive("du_no", measures)
    assert is_additive(" so_hd ", measures)
    # A customer counted in two regions is one customer in the total: shares of a distinct
    # count do not add up to 100 % (semantic/analysis.is_additive agrees).
    assert not is_additive("so_kh", measures)
    assert not is_additive("lai", measures)
    assert not is_additive("no_xau / du_no * 100", measures)
