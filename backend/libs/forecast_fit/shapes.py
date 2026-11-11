"""The shapes a fit takes in and gives back. Apart from fit.py so a caller that only sends a
series to the ml-forecast service (worker-agent's image has no statsforecast) can read them."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Point:
    period: dt.date
    value: float


@dataclass(frozen=True)
class Forecast:
    period: dt.date
    point: float
    lo80: float
    hi80: float
    lo95: float
    hi95: float


@dataclass(frozen=True)
class Candidate:
    algorithm: str
    # None when a figure is not finite (undefined MASE, NaN): the result must survive
    # json.dumps(allow_nan=False). A None MASE never counts as below 1.
    mase: float | None
    smape: float | None  # percent, 0..200
    coverage80: float | None  # share of backtest actuals inside the 80% interval, 0..1
    coverage95: float | None
    in_sample_mase: float | None


@dataclass(frozen=True)
class FitResult:
    chosen: str | None
    reason: str | None
    candidates: list[Candidate]
    cutoffs: list[dt.date]
    predictions: list[Forecast]
    interval_method: str | None  # "model" | "conformal"; None when nothing was chosen
    params: dict[str, Any]
