"""How a single-metric answer is drawn (AC-01-01-01): the spec names the cut, never the values."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.answer.narrate import Figure, chart_spec_of

pytestmark = [pytest.mark.unit]

NAMES = {"vung": "Vùng", "thang": "Tháng", "san_pham": "Sản phẩm"}


def _figs(n: int) -> list[Figure]:
    return [
        Figure(f"F{i + 1}", "x", decimal.Decimal(i), "%", 2, i, "value", {"vung": str(i)})
        for i in range(n)
    ]


def _spec(group_by: list[str], n: int = 3) -> dict | None:
    return chart_spec_of(
        _figs(n),
        group_by=group_by,
        dimension_names=NAMES,
        time_dimensions={"thang"},
        metric_name="Tỷ lệ nợ xấu",
        unit="%",
    )


def test_a_category_cut_is_bars_labelled_from_the_semantic_layer() -> None:
    assert _spec(["vung"]) == {
        "type": "bar",
        "intent": "comparing",
        "x": "vung",
        "x_label": "Vùng",
        "y": "value",
        "y_label": "Tỷ lệ nợ xấu",
        "unit": "%",
    }


def test_a_share_cut_with_few_members_is_a_pie() -> None:
    spec = chart_spec_of(
        _figs(4),
        group_by=["vung"],
        dimension_names=NAMES,
        time_dimensions={"thang"},
        metric_name="Tỷ trọng",
        unit="%",
        value_column="share",
    )
    assert spec is not None
    assert (spec["type"], spec["intent"], spec["y"]) == ("pie", "part_to_whole", "share")


def test_a_time_cut_is_a_line() -> None:
    spec = _spec(["thang"])
    assert spec is not None and spec["type"] == "line"


def test_a_second_cut_is_the_series() -> None:
    spec = _spec(["thang", "san_pham"])
    assert spec is not None
    assert (spec["series"], spec["series_label"]) == ("san_pham", "Sản phẩm")


def test_nothing_to_compare_draws_no_chart() -> None:
    assert _spec([]) is None
    assert _spec(["vung"], n=1) is None
