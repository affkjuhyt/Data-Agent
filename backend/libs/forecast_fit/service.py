"""One door to a fit: the ml-forecast service when FORECAST_SERVICE_URL is set, otherwise `fit`
in this process. In-process is for tests and local runs; worker-agent's image has no statsforecast
and always sets the URL, so `fit` is imported only on that path."""

from __future__ import annotations

import asyncio
import os
from typing import Literal

from .client import UNAVAILABLE, TrainerUnavailable, fit_remote
from .shapes import FitResult, Point

__all__ = ["TrainerUnavailable", "fit_series"]


async def fit_series(
    series: list[Point],
    *,
    frequency: Literal["month", "quarter"],
    horizon: int,
) -> FitResult:
    url = os.environ.get("FORECAST_SERVICE_URL", "").strip()
    if url:
        return await fit_remote(url, series, frequency=frequency, horizon=horizon)
    try:
        from .backtest import fit
    except ImportError as exc:
        raise TrainerUnavailable(UNAVAILABLE) from exc
    return await asyncio.to_thread(fit, series, frequency=frequency, horizon=horizon)
