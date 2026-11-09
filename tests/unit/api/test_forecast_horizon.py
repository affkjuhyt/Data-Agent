"""The horizon a retrain uses when the model has no backtest to copy.

An untrained monthly definition (the DS-AW seed) has nowhere else to store a
horizon: forecast.forecast_model has no such column. The retrain then asks for
one seasonal cycle, which is what those definitions are for.
"""

from __future__ import annotations

import pytest

from services.api.modules.forecast.rules import DEFAULT_HORIZON

pytestmark = pytest.mark.unit


def test_an_untrained_monthly_model_retrains_one_season_ahead() -> None:
    assert DEFAULT_HORIZON["month"] == 12
