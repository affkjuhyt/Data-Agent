"""A steward's acknowledgement of a failing high rule (quality_gate._covered): it holds while the
rule keeps failing the way it was acknowledged, not only until the next scheduled check."""

from __future__ import annotations

import datetime

import pytest

from services.worker_agent import quality_gate as qg

pytestmark = [pytest.mark.unit]

NOW = datetime.datetime(2026, 11, 24, 12, tzinfo=datetime.UTC)
SINCE = NOW - datetime.timedelta(hours=qg.ACK_HOURS)
ACKED = NOW - datetime.timedelta(days=2)


def test_later_checks_failing_alike_stay_acknowledged() -> None:
    # Newest first: two scheduled checks since the acknowledged one, the same one violation.
    assert qg._covered([(False, None, 1), (False, None, 1), (False, ACKED, 1)], SINCE)


def test_more_violations_is_a_new_problem() -> None:
    assert not qg._covered([(False, None, 3), (False, ACKED, 1)], SINCE)


def test_a_pass_in_between_ends_the_acknowledgement() -> None:
    assert not qg._covered([(False, None, 1), (True, None, 0), (False, ACKED, 1)], SINCE)


def test_an_acknowledgement_lapses_after_ack_hours() -> None:
    old = NOW - datetime.timedelta(days=8)
    assert not qg._covered([(False, None, 1), (False, old, 1)], SINCE)
    assert not qg._covered([(False, None, 1)], SINCE)  # never acknowledged


def test_the_window_is_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALITY_ACK_HOURS", "24")
    assert qg.ack_hours() == 24
    monkeypatch.setenv("QUALITY_ACK_HOURS", "x")
    assert qg.ack_hours() == qg.ACK_HOURS
