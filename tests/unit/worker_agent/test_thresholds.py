"""A result judged against the metric's threshold watch (monitor rule_kind threshold): which
members are over it, which are in the early-warning band, written with traced figures."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.answer.insights import evaluate, written
from services.worker_agent.answer.narrate import Figure
from services.worker_agent.answer.thresholds import Threshold, judged, state, threshold_of

pytestmark = [pytest.mark.unit]

D = decimal.Decimal
NPL = Threshold("above", D("3"), D("2"), "> 3% (cảnh báo sớm từ 2%)")


def fig(key: str, member: str | None, value: str | None, state_: str = "shown") -> Figure:
    return Figure(
        key,
        f"Tỷ lệ nợ xấu {member or ''}".strip(),
        D(value) if value is not None else None,
        "%",
        2,
        int(key[1:]) - 1,
        "value",
        dimensions={"nhom_khach_hang": member} if member else {},
        display_state=state_,  # type: ignore[arg-type]
    )


def test_a_monitor_rule_becomes_a_threshold() -> None:
    rule = {"direction": "above", "value": 3, "early": 2, "text": "> 3%"}
    assert threshold_of(rule) == Threshold("above", D("3"), D("2"), "> 3%")
    assert threshold_of({"direction": "below", "value": "95.5"}) == Threshold(
        "below", D("95.5"), None, None
    )


@pytest.mark.parametrize(
    "rule",
    [{}, {"direction": "sideways", "value": 3}, {"direction": "above"}, {"value": "x"}],
)
def test_a_rule_that_is_not_a_threshold_is_none(rule: dict[str, object]) -> None:
    assert threshold_of(rule) is None


def test_an_early_level_on_the_wrong_side_is_ignored() -> None:
    # An early warning above an "above" threshold would never come first.
    assert threshold_of({"direction": "above", "value": 3, "early": 4}) == Threshold(
        "above", D("3"), None, None
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("4.56", "breach"), ("3", "early"), ("2.29", "early"), ("2", "early"), ("0.84", "ok")],
)
def test_above_is_over_the_value_and_early_from_the_early_level(value: str, expected: str) -> None:
    assert state(D(value), NPL) == expected


def test_below_mirrors_above() -> None:
    coverage = Threshold("below", D("100"), D("110"), None)
    assert [state(D(v), coverage) for v in ("95", "105", "120")] == ["breach", "early", "ok"]


def test_members_are_grouped_by_where_they_stand_with_traced_numbers() -> None:
    figures = [
        fig("F1", "Cá nhân", "2.29"),
        fig("F2", "Doanh nghiệp lớn", "0.84"),
        fig("F3", "SME", "4.56"),
    ]
    out = judged(figures, NPL, metric_name="Tỷ lệ nợ xấu", next_key=4)
    # The threshold and its early level are figures too, so no number in the sentence is untraced.
    assert [(f.key, f.value, f.expr) for f in out.figures] == [
        ("F4", D("3"), "3"),
        ("F5", D("2"), "2"),
    ]
    assert out.sentences == [
        "So với ngưỡng theo dõi ({{F4}}): vượt ngưỡng: SME ({{F3}}); cảnh báo sớm (từ {{F5}}): "
        "Cá nhân ({{F1}}); trong giới hạn: Doanh nghiệp lớn."
    ]
    text = written(out.sentences, figures + out.figures)
    assert text.startswith("So với ngưỡng theo dõi (3,00%): vượt ngưỡng: SME (4,56%)")
    assert evaluate(out.figures[0].expr or "", {}) == D("3")


def test_one_figure_says_where_it_stands() -> None:
    out = judged([fig("F1", None, "1.63")], NPL, metric_name="Tỷ lệ nợ xấu", next_key=2)
    assert out.sentences == ["Tỷ lệ nợ xấu trong giới hạn so với ngưỡng theo dõi ({{F2}})."]
    out = judged([fig("F1", None, "3.4")], NPL, metric_name="Tỷ lệ nợ xấu", next_key=2)
    assert out.sentences == ["Tỷ lệ nợ xấu vượt ngưỡng theo dõi ({{F2}})."]


def test_nothing_is_judged_when_a_figure_is_hidden() -> None:
    # BR-178: saying a masked member is over the threshold would reveal it.
    figures = [fig("F1", "Cá nhân", None, "masked_stale"), fig("F2", "SME", "4.56")]
    out = judged(figures, NPL, metric_name="Tỷ lệ nợ xấu", next_key=3)
    assert out.sentences == [] and out.figures == []
