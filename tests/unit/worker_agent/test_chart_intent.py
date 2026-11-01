"""Chart intent → type among the eight FE renderers (AN-03 precursor)."""

from __future__ import annotations

import pytest

from services.worker_agent.answer.chart_intent import (
    Intent,
    classify,
    recommend_type,
)

pytestmark = [pytest.mark.unit]


def test_a_time_cut_is_time_intent_and_a_line() -> None:
    intent = classify(group_by=["thang"], time_dimensions={"thang"}, value_column="value")
    assert intent == Intent.TIME
    assert recommend_type(intent, n_members=12) == "line"


def test_a_category_cut_is_comparing_bars() -> None:
    intent = classify(group_by=["vung"], time_dimensions={"thang"}, value_column="value")
    assert intent == Intent.COMPARING
    assert recommend_type(intent, n_members=5) == "bar"


def test_a_share_column_with_few_members_is_part_to_whole_pie() -> None:
    intent = classify(group_by=["vung"], time_dimensions=set(), value_column="share")
    assert intent == Intent.PART_TO_WHOLE
    assert recommend_type(intent, n_members=4) == "pie"


def test_part_to_whole_with_many_members_or_a_series_stays_bars() -> None:
    intent = classify(group_by=["vung"], time_dimensions=set(), value_column="share")
    assert recommend_type(intent, n_members=20) == "bar"
    assert recommend_type(intent, n_members=4, has_series=True) == "bar"


def test_cumulative_along_time_is_an_area() -> None:
    intent = classify(group_by=["thang"], time_dimensions={"thang"}, value_column="cumulative")
    assert intent == Intent.TIME
    assert recommend_type(intent, n_members=8, value_column="cumulative") == "area"


def test_two_cuts_without_time_stay_grouped_bars() -> None:
    intent = classify(
        group_by=["vung", "san_pham"], time_dimensions={"thang"}, value_column="value"
    )
    assert intent == Intent.COMPARING
    assert recommend_type(intent, n_members=6, has_series=True) == "bar"
