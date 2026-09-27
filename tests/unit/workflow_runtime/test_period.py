"""Unit tests for backend/libs/indicator/period.py.

The period is what makes the stock/flow check in formula.py possible: without a
visible frequency there is no way to tell that a formula aligned on calendar
years was asked about a quarter.
"""
from __future__ import annotations

import pytest

from libs.indicator.period import Frequency, Period, PeriodError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("text", "year", "frequency", "index"), [
    ("2024", 2024, Frequency.ANNUAL, 0),
    ("2024-Q2", 2024, Frequency.QUARTERLY, 2),
    ("2024-06", 2024, Frequency.MONTHLY, 6),
    ("2024-12", 2024, Frequency.MONTHLY, 12),
])
def test_parses_the_three_spellings_the_store_uses(text, year, frequency, index):
    period = Period.parse(text)

    assert (period.year, period.frequency, period.index) == (year, frequency, index)


@pytest.mark.parametrize("text", [
    "", "24", "2024-Q5", "2024-Q0", "2024-13", "2024-00", "2024/06", "Q2-2024", "2024-6",
])
def test_rejects_anything_else(text):
    with pytest.raises(PeriodError):
        Period.parse(text)


@pytest.mark.parametrize("text", ["2024", "2024-Q2", "2024-06"])
def test_round_trips_through_its_own_spelling(text):
    assert str(Period.parse(text)) == text


def test_previous_steps_back_a_full_year_at_every_frequency():
    """Year-over-year, not quarter-over-quarter: fiscal series are seasonal, and
    2024-Q2 against 2024-Q1 answers a different question than the one callers
    of `previous()` are asking."""
    assert str(Period.parse("2024").previous()) == "2023"
    assert str(Period.parse("2024-Q2").previous()) == "2023-Q2"
    assert str(Period.parse("2024-06").previous()) == "2023-06"


def test_periods_of_one_frequency_order_by_time():
    quarters = [Period.parse(t) for t in ("2024-Q3", "2023-Q1", "2024-Q1")]

    assert [str(p) for p in sorted(quarters)] == ["2023-Q1", "2024-Q1", "2024-Q3"]
