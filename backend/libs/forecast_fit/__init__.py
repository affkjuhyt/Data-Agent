"""Fitting for forecasts: backtest candidates against seasonal naive, keep the best.

Pure computation (no database, network or file I/O), so the stateless ml-forecast service
and a local fallback call the same code and get the same answer.

`fit` is loaded on first use: it pulls statsforecast, which worker-agent's image does not
carry (it calls the service). The shapes and the client import without it.
"""

from __future__ import annotations

import importlib
from typing import Any

from .shapes import Candidate, FitResult, Forecast, Point

__all__ = ["Candidate", "FitResult", "Forecast", "Point", "fit"]


def __getattr__(name: str) -> Any:
    if name == "fit":
        # The module is `backtest`, not `fit`: a submodule of this name would, once imported,
        # replace this attribute with itself.
        return importlib.import_module(".backtest", __name__).fit
    raise AttributeError(name)
