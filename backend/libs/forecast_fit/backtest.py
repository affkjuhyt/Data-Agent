"""Rolling-origin backtest of five candidates; the winner must beat seasonal naive.

Candidates are naive, seasonal naive, ETS, Theta and ARIMA (statsforecast, Apache-2.0).
The two baselines are measured but never chosen: a model is worth keeping only if its
MASE, scaled by the in-sample seasonal-naive error, is below 1 across the backtest,
and the figures that would be stored would pass forecast.model_version_guard (BR-172).
Messages that reach a user are Vietnamese; everything else is English.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from typing import Any, Literal

import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA, AutoETS, AutoTheta, Naive, SeasonalNaive

from .guard import UNPUBLISHABLE, passes_publish_guard
from .shapes import Candidate, FitResult, Forecast, Point

MIN_POINTS = 24
# Shortest history a backtest window may train on; below it ETS/ARIMA fit noise.
MIN_TRAIN = 12
MAX_CUTOFFS = 12
LEVELS = (80, 95)
BASELINES = ("naive", "seasonal_naive")
ALGORITHMS = ("naive", "seasonal_naive", "ets", "theta", "arima")

_SEASON = {"month": 12, "quarter": 4}
_FREQ = {"month": "MS", "quarter": "QS"}
_MONTHS_PER_PERIOD = {"month": 1, "quarter": 3}


def _models(m: int) -> dict[str, Any]:
    # Aliases are the statsforecast column names; fixed so the output never depends on
    # a library default.
    return {
        "naive": Naive(alias="naive"),
        "seasonal_naive": SeasonalNaive(season_length=m, alias="seasonal_naive"),
        "ets": AutoETS(season_length=m, alias="ets"),
        "theta": AutoTheta(season_length=m, alias="theta"),
        "arima": AutoARIMA(season_length=m, alias="arima"),
    }


def _validate(series: list[Point]) -> None:
    if len(series) < MIN_POINTS:
        raise ValueError(f"Cần ít nhất {MIN_POINTS} kỳ để dự báo, chuỗi này có {len(series)} kỳ.")
    for prev, cur in itertools.pairwise(series):
        if cur.period == prev.period:
            raise ValueError(f"Kỳ {cur.period.isoformat()} xuất hiện nhiều lần trong chuỗi.")
        if cur.period < prev.period:
            raise ValueError("Các kỳ trong chuỗi phải được sắp xếp tăng dần.")
    if any(not math.isfinite(p.value) for p in series):
        raise ValueError("Chuỗi có giá trị không phải số hữu hạn.")


def _next_periods(last: dt.date, frequency: str, count: int) -> list[dt.date]:
    step = _MONTHS_PER_PERIOD[frequency]
    out = []
    for i in range(1, count + 1):
        months = last.year * 12 + (last.month - 1) + step * i
        out.append(dt.date(months // 12, months % 12 + 1, 1))
    return out


def _seasonal_naive_scale(values: np.ndarray, m: int) -> float:
    return float(np.mean(np.abs(values[m:] - values[:-m])))


def _finite(x: float) -> float | None:
    f = float(x)
    return f if math.isfinite(f) else None


def _mase(errors: np.ndarray, scale: float, tolerance: float) -> float:
    mae = float(np.mean(np.abs(errors)))
    if scale <= tolerance:
        # Seasonal naive is perfect in sample: the ratio is undefined, and no model can
        # be shown to beat it (float noise of 1e-15 is not an improvement).
        return 0.0 if mae <= tolerance else math.inf
    return mae / scale


def _smape(y: np.ndarray, yhat: np.ndarray) -> float:
    denom = np.abs(y) + np.abs(yhat)
    safe = np.where(denom == 0, 1.0, denom)
    return float(np.mean(np.where(denom == 0, 0.0, 200.0 * np.abs(y - yhat) / safe)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, np.floating | float):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, bool | int | str) or value is None:
        return value
    return str(value)


def _fitted_params(sf: StatsForecast, algorithm: str, m: int) -> dict[str, Any]:
    """The chosen model's own fitted parameters, as far as statsforecast exposes them."""
    params: dict[str, Any] = {"season_length": m}
    try:
        fitted = sf.fitted_[0, list(ALGORITHMS).index(algorithm)]
        model = getattr(fitted, "model_", None)
        if isinstance(model, dict):
            keep = {
                k: v
                for k, v in model.items()
                if k
                in {
                    "method",
                    "par",
                    "arma",
                    "aic",
                    "aicc",
                    "bic",
                    "sigma2",
                    "mse",
                    "theta",
                    "alpha",
                    "smoothing_level",
                }
                or k.startswith("alpha")
            }
            params.update(_json_safe(keep))
    except (AttributeError, IndexError, KeyError, TypeError):
        pass
    return params


def _conformal(errors: np.ndarray, point: np.ndarray, level: int) -> tuple[np.ndarray, np.ndarray]:
    # Symmetric interval from the empirical quantile of the model's own backtest errors.
    q = float(np.quantile(np.abs(errors), level / 100, method="higher"))
    return point - q, point + q


def _nothing(reason: str, candidates: list[Candidate], cutoffs: list[dt.date]) -> FitResult:
    return FitResult(
        chosen=None,
        reason=reason,
        candidates=candidates,
        cutoffs=cutoffs,
        predictions=[],
        interval_method=None,
        params={},
    )


def fit(
    series: list[Point],
    *,
    frequency: Literal["month", "quarter"],
    horizon: int,
    min_cutoffs: int = 6,
) -> FitResult:
    _validate(series)
    if horizon < 1:
        raise ValueError("Tầm dự báo phải ít nhất 1 kỳ.")
    m = _SEASON[frequency]
    n = len(series)
    n_windows = min(MAX_CUTOFFS, n - horizon - MIN_TRAIN)
    n_windows = max(n_windows, 0)
    if n_windows < min_cutoffs:
        largest = n - MIN_TRAIN - min_cutoffs
        raise ValueError(
            f"Chuỗi {n} kỳ quá ngắn để kiểm định {min_cutoffs} mốc với tầm dự báo {horizon} kỳ; "
            f"với chuỗi này, tầm dự báo tối đa là {max(largest, 0)} kỳ."
        )

    df = pd.DataFrame(
        {
            "unique_id": "s",
            "ds": pd.to_datetime([p.period for p in series]),
            "y": [float(p.value) for p in series],
        }
    )
    values = df["y"].to_numpy()
    scale = _seasonal_naive_scale(values, m)
    tolerance = 1e-9 * max(1.0, float(np.mean(np.abs(values))))
    models = _models(m)
    sf = StatsForecast(models=list(models.values()), freq=_FREQ[frequency], n_jobs=1)

    cv = sf.cross_validation(
        df=df, h=horizon, n_windows=n_windows, step_size=1, level=list(LEVELS)
    ).sort_values(["cutoff", "ds"])
    cutoffs = sorted({c.date() for c in pd.to_datetime(cv["cutoff"])})
    y = cv["y"].to_numpy()

    # In-sample fit comes from forecast(fitted=True); fit() keeps the model objects whose
    # parameters and prediction are read below. Two fits of a short series are cheap.
    sf.forecast(df=df, h=horizon, fitted=True)
    in_sample = sf.forecast_fitted_values()
    sf.fit(df)

    def interval(
        frame: pd.DataFrame, name: str, level: int
    ) -> tuple[np.ndarray, np.ndarray] | None:
        lo, hi = f"{name}-lo-{level}", f"{name}-hi-{level}"
        if lo not in frame or hi not in frame:
            return None
        a, b = frame[lo].to_numpy(dtype=float), frame[hi].to_numpy(dtype=float)
        return (a, b) if np.isfinite(a).all() and np.isfinite(b).all() else None

    candidates: list[Candidate] = []
    for name in ALGORITHMS:
        yhat = cv[name].to_numpy(dtype=float)
        errors = y - yhat
        coverage = {}
        for level in LEVELS:
            bounds = interval(cv, name, level)
            if bounds is None:
                bounds = _conformal(errors, yhat, level)
            coverage[level] = float(np.mean((y >= bounds[0]) & (y <= bounds[1])))
        fitted_y = in_sample["y"].to_numpy(dtype=float)
        fitted_hat = in_sample[name].to_numpy(dtype=float)
        ok = np.isfinite(fitted_hat)
        candidates.append(
            Candidate(
                algorithm=name,
                mase=_finite(_mase(errors, scale, tolerance)),
                smape=_finite(_smape(y, yhat)),
                coverage80=_finite(coverage[80]),
                coverage95=_finite(coverage[95]),
                in_sample_mase=_finite(
                    _mase(fitted_y[ok] - fitted_hat[ok], scale, tolerance) if ok.any() else math.inf
                ),
            )
        )

    # MASE < 1 is the seasonal-naive bar (BR-158). Publish also needs a positive in-sample
    # MASE at 4 decimals and a backtest MASE no more than twice that (BR-172). A candidate
    # that fails the second check must not win: the job would still open a proposal, and
    # accepting it raises.
    beating = [
        c
        for c in candidates
        if scale > tolerance and c.algorithm not in BASELINES and c.mase is not None and c.mase < 1
    ]
    if not beating:
        # Plan / Review Focus wording — keep the seasonal-naive phrase so jobs and tests match.
        return _nothing(
            "Không mô hình nào tốt hơn cách đoán theo mùa (seasonal naive)",
            candidates,
            cutoffs,
        )
    eligible = [c for c in beating if passes_publish_guard(c.mase, c.in_sample_mase)]
    if not eligible:
        return _nothing(UNPUBLISHABLE, candidates, cutoffs)

    future = sf.predict(h=horizon, level=list(LEVELS))
    # Best backtest first; a candidate whose forecast holds a non-finite value is skipped.
    for best in sorted(eligible, key=lambda c: (c.mase, ALGORITHMS.index(c.algorithm))):
        point = future[best.algorithm].to_numpy(dtype=float)
        bounds_by_level: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        method = "model"
        for level in LEVELS:
            found = interval(future, best.algorithm, level)
            if found is None:
                method = "conformal"
                cv_err = y - cv[best.algorithm].to_numpy(dtype=float)
                found = _conformal(cv_err, point, level)
            bounds_by_level[level] = found
        if np.isfinite(point).all() and all(
            np.isfinite(b).all() for lh in bounds_by_level.values() for b in lh
        ):
            break
    else:
        return _nothing(
            "Mô hình tốt nhất cho ra giá trị dự báo không hợp lệ, nên không tạo dự báo.",
            candidates,
            cutoffs,
        )

    periods = _next_periods(series[-1].period, frequency, horizon)
    predictions = []
    for i, period in enumerate(periods):
        lo80, hi80 = bounds_by_level[80][0][i], bounds_by_level[80][1][i]
        lo95, hi95 = bounds_by_level[95][0][i], bounds_by_level[95][1][i]
        p = float(point[i])
        # Models should already nest; the clamp guards a rounding crossing.
        lo80, hi80 = min(float(lo80), p), max(float(hi80), p)
        lo95, hi95 = min(float(lo95), lo80), max(float(hi95), hi80)
        predictions.append(Forecast(period, p, lo80, hi80, lo95, hi95))

    return FitResult(
        chosen=best.algorithm,
        reason=None,
        candidates=candidates,
        cutoffs=cutoffs,
        predictions=predictions,
        interval_method=method,
        params=_fitted_params(sf, best.algorithm, m),
    )
