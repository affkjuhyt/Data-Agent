"""MAD outlier skill (skills/anomaly.py)."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.skills.anomaly import NotEnough, Point, find_outliers, result_of

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


def _spread_with_spike() -> list[Point]:
    # MAD is zero when almost every value equals the median; need real spread.
    base = [D(8), D(9), D(10), D(11), D(12), D(13)]
    return [Point(str(i), v) for i, v in enumerate(base)] + [Point("spike", D(100))]


def test_a_clear_outlier_is_flagged() -> None:
    found = find_outliers(_spread_with_spike())
    assert [f.member for f in found] == ["spike"]


def test_too_few_members_are_refused() -> None:
    with pytest.raises(NotEnough):
        find_outliers([Point("a", D(1)), Point("b", D(2))])


def test_result_names_the_outlier_without_claiming_cause() -> None:
    result = result_of(
        _spread_with_spike(),
        metric_name="Doanh thu",
        dimension_name="Vùng",
        unit="đồng",
        decimals=0,
    )
    assert "spike" in result["summary"]
    assert "không phải nguyên nhân" in result["summary"]
    assert result["anomaly"]["outliers"][0]["member"] == "spike"
