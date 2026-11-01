"""Funnel conversion skill (skills/funnel.py)."""

from __future__ import annotations

import decimal

import pytest

from services.worker_agent.skills.funnel import BadFunnel, Stage, conversions, result_of

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


def test_step_rates_and_overall() -> None:
    stages = [
        Stage("Visit", D(1000)),
        Stage("Sign-up", D(200)),
        Stage("Paid", D(50)),
    ]
    steps = conversions(stages)
    assert [format(s.rate, "f") for s in steps] == ["0.2", "0.25"]
    result = result_of(stages)
    assert result["funnel"]["overall_rate"] == "0.05"
    assert "5,0%" in result["summary"] or "5.0%" in result["summary"]


def test_an_increase_is_refused_unless_allowed() -> None:
    stages = [Stage("A", D(10)), Stage("B", D(20))]
    with pytest.raises(BadFunnel):
        conversions(stages)
    assert conversions(stages, allow_increase=True)[0].rate == D(2)
