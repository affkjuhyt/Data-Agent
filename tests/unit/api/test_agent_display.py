"""The sentences the agent list draws from a config, a trigger and a week of runs."""

from __future__ import annotations

import pytest

from services.api.modules.agent.display import permission_text, runs_window, trigger_text

pytestmark = pytest.mark.unit


def test_permission_text_keeps_a_fixed_order() -> None:
    assert permission_text(["send_external", "read"]) == "Đọc · Gửi ra ngoài"
    assert permission_text([]) is None


def test_trigger_text_says_a_monday_morning_schedule() -> None:
    assert trigger_text([("schedule", "0 7 * * 1", None)]) == "Theo lịch 07:00 thứ Hai"
    assert trigger_text([("event", None, "period.closed"), ("manual", None, None)]) == (
        "Khi có sự kiện period.closed · Khi được giao"
    )
    assert trigger_text([]) is None


def test_runs_window_is_empty_until_the_agent_has_run() -> None:
    assert runs_window(0, 0, 0) is None
    assert runs_window(4, 1, 1) == {"count": 4, "needs_approval_pct": 25, "errors": 1}
