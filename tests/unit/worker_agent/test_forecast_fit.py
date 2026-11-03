"""libs/forecast_fit: a backtest against seasonal naive decides whether a model is kept."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import random

import pytest

from libs.forecast_fit import Point, fit

pytestmark = pytest.mark.unit


def _months(n: int, start: dt.date = dt.date(2020, 1, 1)) -> list[dt.date]:
    return [
        dt.date(start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1, 1)
        for i in range(n)
    ]


def _quarters(n: int) -> list[dt.date]:
    return [dt.date(2015 + i // 4, (i % 4) * 3 + 1, 1) for i in range(n)]


def _seasonal_series(n: int) -> list[Point]:
    rng = random.Random(7)
    return [
        Point(p, 100 + 2.0 * i + 15 * math.sin(2 * math.pi * i / 12) + rng.gauss(0, 3))
        for i, p in enumerate(_months(n))
    ]


def test_a_seasonal_series_chooses_a_model_that_beats_seasonal_naive():
    result = fit(_seasonal_series(48), frequency="month", horizon=6)

    assert result.chosen in {"ets", "theta", "arima"}
    chosen = next(c for c in result.candidates if c.algorithm == result.chosen)
    assert chosen.mase < 1
    assert {c.algorithm for c in result.candidates} == {
        "naive",
        "seasonal_naive",
        "ets",
        "theta",
        "arima",
    }
    assert len(result.predictions) == 6
    for f in result.predictions:
        assert f.lo95 <= f.lo80 <= f.point <= f.hi80 <= f.hi95
    assert result.predictions[0].period == dt.date(2024, 1, 1)
    assert len(result.cutoffs) >= 6
    assert result.interval_method in {"model", "conformal"}


def test_nothing_beats_seasonal_naive_chooses_nothing():
    pattern = [10, 12, 15, 20, 18, 14, 11, 9, 13, 17, 19, 16]
    series = [Point(p, float(pattern[i % 12])) for i, p in enumerate(_months(48))]

    result = fit(series, frequency="month", horizon=6)

    assert result.chosen is None
    assert result.reason is not None and result.reason.startswith("Không mô hình nào tốt hơn")
    assert result.predictions == []


def test_too_few_points_is_refused_in_words():
    with pytest.raises(ValueError, match="ít nhất 24 kỳ"):
        fit(_seasonal_series(20), frequency="month", horizon=3)


def test_an_unsorted_or_duplicate_period_is_refused():
    series = _seasonal_series(30)
    with pytest.raises(ValueError):
        fit(list(reversed(series)), frequency="month", horizon=3)
    with pytest.raises(ValueError):
        fit([*series[:-1], series[-2]], frequency="month", horizon=3)


def test_quarter_uses_season_length_four():
    rng = random.Random(3)
    series = [
        Point(p, 200 + 3.0 * i + 25 * math.sin(2 * math.pi * i / 4) + rng.gauss(0, 4))
        for i, p in enumerate(_quarters(40))
    ]

    result = fit(series, frequency="quarter", horizon=4)

    assert len(result.predictions) == 4
    assert result.predictions[0].period == dt.date(2025, 1, 1)
    assert result.predictions[1].period == dt.date(2025, 4, 1)
    assert result.params.get("season_length") == 4


def test_the_result_is_deterministic():
    series = _seasonal_series(36)

    assert fit(series, frequency="month", horizon=3) == fit(series, frequency="month", horizon=3)


def test_every_result_survives_strict_json():
    pattern = [10, 12, 15, 20, 18, 14, 11, 9, 13, 17, 19, 16]
    perfect = [Point(p, float(pattern[i % 12])) for i, p in enumerate(_months(48))]
    for series in (_seasonal_series(48), perfect):
        result = fit(series, frequency="month", horizon=6)
        json.dumps(dataclasses.asdict(result), allow_nan=False, default=str)
    assert result.interval_method is None
    assert all(
        c.mase is None or c.mase == 0
        for c in result.candidates
        if c.algorithm not in {"seasonal_naive"}
    )


def test_a_horizon_too_long_names_the_longest_allowed():
    with pytest.raises(ValueError, match="tối đa là 6 kỳ"):
        fit(_seasonal_series(24), frequency="month", horizon=9)
