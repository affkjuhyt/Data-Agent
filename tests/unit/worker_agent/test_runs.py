"""The agent run executor's rules, without a database: which events start work, the limits a
run keeps (BR-170), call fingerprints and the hard loop (AC-13-02-09), and tool authority under
a test run (UC-13-04 step 1)."""

from __future__ import annotations

import datetime

import pytest

from services.worker_agent import runs

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ("status", "previous", "wanted"),
    [
        ("queued", None, True),  # created by the api (test run, trigger, onboarding)
        ("queued", "failed", True),  # a retry
        ("running", "paused", True),  # resumed mid-way
        ("running", "waiting_human", True),  # its checkpoint was decided
        ("queued", "paused", True),  # resumed before it started
        ("planning", "queued", False),  # the executor's own transition
        ("running", "planning", False),
        ("paused", "running", False),
        ("cancelled", "running", False),
        ("succeeded", "running", False),
    ],
)
def test_only_a_run_that_is_ready_to_go_is_picked_up(
    status: str, previous: str | None, wanted: bool
) -> None:
    assert runs.wants_execution({"status": status, "previous_status": previous}) is wanted


def test_limits_only_lower_the_ceilings() -> None:
    assert runs.effective_limits({}) == runs.Limits(60, 400_000, 30 * 60)
    assert runs.effective_limits({"max_steps": 5, "max_duration_min": 2}) == runs.Limits(
        5, 400_000, 120
    )
    # A config that slipped past the api's check still never raises a ceiling.
    assert runs.effective_limits({"max_steps": 999, "max_tokens": "x"}).max_steps == 60


def test_a_fingerprint_ignores_key_order_and_request_noise() -> None:
    a = runs.fingerprint(
        "query.semantic",
        {"period": [datetime.date(2026, 9, 1)], "group_by": ["vung"], "request_id": "r1"},
    )
    b = runs.fingerprint(
        "query.semantic",
        {"group_by": ["vung"], "period": ["2026-09-01"], "request_id": "r2"},
    )
    assert a == b and len(a) == 64
    assert a != runs.fingerprint("query.semantic", {"group_by": ["san_pham"]})


def test_the_same_call_with_the_same_result_three_times_is_a_loop() -> None:
    same = ("f", "h")
    assert runs.is_loop([same, same]) is False
    assert runs.is_loop([same, ("f", "other"), same, same]) is True
    # The same call with a changing result is progress, not a loop.
    assert runs.is_loop([("f", "h1"), ("f", "h2"), ("f", "h3")]) is False


@pytest.mark.parametrize(
    ("tool", "level", "is_test", "expected"),
    [
        ("query.semantic", "read", False, "run"),
        ("query.semantic", "read", True, "run"),
        # The builder's draft names the read tool "query".
        ("query", "read", False, "run"),
        ("report.render", "write_derived", True, "simulate"),
        ("notify.external", "send_external", True, "simulate"),
        ("report.render", "write_derived", False, "run"),
    ],
)
def test_a_granted_tool_runs_and_a_test_run_only_simulates_writes(
    tool: str, level: str, is_test: bool, expected: str
) -> None:
    granted = {tool: level}
    wanted = "query.semantic" if tool == "query" else tool
    assert runs.authority(wanted, granted, is_test=is_test) == expected


def test_a_tool_the_agent_was_not_given_is_denied() -> None:
    assert runs.authority("query.semantic", {"doc.search": "read"}, is_test=False) == "denied"


def test_a_narrative_entry_is_what_the_run_screen_draws() -> None:
    at = datetime.datetime(2026, 10, 17, 9, 0, tzinfo=datetime.UTC)
    assert runs.narrative("Đang tính Tỷ lệ nợ xấu", "now", at) == {
        "at": "2026-10-17T09:00:00+00:00",
        "text": "Đang tính Tỷ lệ nợ xấu",
        "state": "now",
    }


@pytest.mark.parametrize(
    ("level", "autonomy", "checkpoints", "requires", "expected"),
    [
        # BR-70: writing derived data stops for a person unless the agent acts then reports.
        ("write_derived", "propose_approve", ["before_write_derived"], False, True),
        ("write_derived", "suggest_only", [], True, True),
        ("write_derived", "act_then_report", [], False, False),
        ("write_derived", "propose_approve", [], False, False),
        # Sending outside always stops (AC-13-02-05), whatever the autonomy.
        ("send_external", "act_then_report", [], False, True),
        ("read", "propose_approve", ["before_write_derived"], False, False),
    ],
)
def test_which_steps_stop_at_a_checkpoint(
    level: str, autonomy: str, checkpoints: list[str], requires: bool, expected: bool
) -> None:
    assert (
        runs.needs_checkpoint(
            level, autonomy=autonomy, checkpoints=checkpoints, requires_checkpoint=requires
        )
        is expected
    )
