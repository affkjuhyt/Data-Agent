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


def _predict_nan_for(monkeypatch, algorithms: set[str]):
    from statsforecast import StatsForecast

    real = StatsForecast.predict

    def predict(self, *args, **kwargs):
        out = real(self, *args, **kwargs)
        for column in out.columns:
            if column.split("-")[0] in algorithms:
                out[column] = float("nan")
        return out

    monkeypatch.setattr(StatsForecast, "predict", predict)


def test_a_candidate_forecasting_nan_is_skipped_for_the_next_best(monkeypatch):
    series = _seasonal_series(48)
    first = fit(series, frequency="month", horizon=6).chosen
    _predict_nan_for(monkeypatch, {first})

    result = fit(series, frequency="month", horizon=6)

    assert result.chosen != first
    if result.chosen is not None:
        assert all(math.isfinite(f.point) for f in result.predictions)


def test_when_every_forecast_is_invalid_nothing_is_chosen(monkeypatch):
    _predict_nan_for(monkeypatch, {"ets", "theta", "arima"})

    result = fit(_seasonal_series(48), frequency="month", horizon=6)

    assert result.chosen is None
    assert (
        result.reason
        == "Mô hình tốt nhất cho ra giá trị dự báo không hợp lệ, nên không tạo dự báo."
    )


# model_version_guard (BR-172) refuses publish when in_sample_mase is missing or not a
# positive numeric(8, 4), or when the stored backtest MASE is more than twice it.
UNPUBLISHABLE = (
    "Không mô hình nào đủ điều kiện phát hành: mô hình tốt hơn cách dự báo đơn giản "
    "vẫn thiếu MASE trong mẫu hoặc quá khớp (MASE backtest quá 2 lần MASE trong mẫu), "
    "nên không tạo dự báo."
)


def _force_scores(monkeypatch, scores: dict[str, tuple[float | None, float | None]]) -> None:
    """Replace each candidate's MASE pair after the real backtest, and keep forecasts finite."""
    from statsforecast import StatsForecast

    import libs.forecast_fit.backtest as backtest

    real_candidate = backtest.Candidate
    real_predict = StatsForecast.predict

    def candidate(*args, **kwargs):
        made = real_candidate(*args, **kwargs)
        forced = scores.get(made.algorithm)
        if forced is None:
            return made
        return dataclasses.replace(made, mase=forced[0], in_sample_mase=forced[1])

    def predict(self, *args, **kwargs):
        return real_predict(self, *args, **kwargs).fillna(0.0)

    monkeypatch.setattr(backtest, "Candidate", candidate)
    monkeypatch.setattr(StatsForecast, "predict", predict)


def test_a_lower_backtest_mase_the_publish_guard_would_refuse_is_not_chosen(monkeypatch):
    # ets wins the backtest but has no in-sample MASE; theta beats seasonal naive but is
    # more than twice as bad out of sample as in sample. arima is worse on the backtest
    # and sits exactly on the guard's 2x line, which publish still allows.
    _force_scores(
        monkeypatch,
        {"ets": (0.20, None), "theta": (0.30, 0.10), "arima": (0.40, 0.20)},
    )

    result = fit(_seasonal_series(48), frequency="month", horizon=6)

    assert result.chosen == "arima"
    assert result.reason is None
    assert len(result.predictions) == 6


def test_when_nothing_publishable_remains_nothing_is_chosen(monkeypatch):
    # Every model beats seasonal naive. None can be published: missing in-sample MASE,
    # backtest more than twice the in-sample one, and an in-sample MASE that rounds to 0
    # on numeric(8, 4) (the column's CHECK refuses 0, so publish sees NULL).
    _force_scores(
        monkeypatch,
        {"ets": (0.20, None), "theta": (0.30, 0.10), "arima": (0.40, 0.00004)},
    )

    result = fit(_seasonal_series(48), frequency="month", horizon=6)

    assert result.chosen is None
    assert result.reason == UNPUBLISHABLE
    assert result.predictions == []
