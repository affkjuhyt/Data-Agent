"""Fitting for forecasts: backtest candidates against seasonal naive, keep the best.

Pure computation (no database, network or file I/O), so the stateless ml-forecast service
and a local fallback call the same code and get the same answer.
"""

from .fit import Candidate, FitResult, Forecast, Point, fit

__all__ = ["Candidate", "FitResult", "Forecast", "Point", "fit"]
