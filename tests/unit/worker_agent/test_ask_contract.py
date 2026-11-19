"""Request contract for ask semantic planner: whether a question needs a metric."""

from __future__ import annotations

import pytest

from services.worker_agent.ask.contract import (
    NumericIntent,
    Purpose,
    RequestContract,
)

pytestmark = pytest.mark.unit


def test_an_overview_does_not_ask_for_a_metric() -> None:
    contract = RequestContract(Purpose.ORIENT, NumericIntent.NONE)
    assert contract.needs_metric_choice() is False


def test_a_required_number_with_no_metric_asks() -> None:
    contract = RequestContract(
        Purpose.ANSWER, NumericIntent.REQUIRED, missing_required_slots=("metric",)
    )
    assert contract.needs_metric_choice() is True
