"""The five-field cron the scheduler fires on."""

from __future__ import annotations

import datetime

import pytest

from services.worker_agent.cron import matches

pytestmark = [pytest.mark.unit]

AT = datetime.datetime(2026, 10, 19, 6, 30, tzinfo=datetime.UTC)  # a Monday


@pytest.mark.parametrize(
    ("expr", "fires"),
    [
        ("* * * * *", True),
        ("30 6 * * *", True),
        ("31 6 * * *", False),
        ("*/15 * * * *", True),
        ("0,30 6-8 * * 1-5", True),
        ("30 6 * * sat,sun", False),
        ("30 6 19 10 *", True),
        ("30 6 1 * 1", True),  # either day field matches when both are restricted
        ("30 6 1 * 2", False),
        ("30 6 * * 7", False),
    ],
)
def test_matches(expr: str, fires: bool) -> None:
    assert matches(expr, AT) is fires


def test_sunday_is_0_and_7() -> None:
    sunday = AT + datetime.timedelta(days=6)
    assert matches("30 6 * * 0", sunday) and matches("30 6 * * 7", sunday)


def test_a_malformed_expression_is_refused() -> None:
    with pytest.raises(ValueError):
        matches("61 * * * *", AT)
    with pytest.raises(ValueError):
        matches("* * *", AT)
