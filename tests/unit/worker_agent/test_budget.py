"""The agent turn's budget (agentic/budget.py, AE-02): tokens, seconds and steps by who asked;
spent, the turn composes from what it found and its run records why it stopped."""

from __future__ import annotations

import types
import uuid

import pytest

from services.worker_agent import turn_trace
from services.worker_agent.agentic import budget, engine
from services.worker_agent.agentic import plan as p

pytestmark = [pytest.mark.unit]


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def trace() -> turn_trace.Trace:
    t = turn_trace.Trace(uuid.uuid4(), uuid.uuid4(), None)
    token = turn_trace._current.set(t)
    yield t
    turn_trace._current.reset(token)


def test_the_limits_follow_the_audience_and_depth() -> None:
    assert budget.Budget.for_("executive", deep=False).limits == budget.Limits(15_000, 30, 6)
    assert budget.Budget.for_("analyst", deep=False).limits == budget.Limits(40_000, 60, 8)
    assert budget.Budget.for_("executive", deep=True).limits == budget.Limits(120_000, 180, 12)
    assert budget.Budget.for_("someone", deep=False).limits == budget.LIMITS["analyst"]


def test_tokens_are_the_turns_own_from_when_the_budget_was_set(trace: turn_trace.Trace) -> None:
    trace.tokens = 5_000  # spent before the agent started (another engine, a model reader)
    b = budget.Budget.for_("executive", deep=False, clock=Clock())
    trace.tokens = 19_999
    assert b.tokens() == 14_999 and b.spent(0) is None
    trace.tokens = 20_000
    assert b.spent(0) == "budget_tokens"


def test_time_and_steps_stop_a_turn_too_and_the_first_reason_stays(trace: turn_trace.Trace) -> None:
    clock = Clock()
    b = budget.Budget.for_("analyst", deep=False, clock=clock)
    assert b.spent(7) is None
    assert b.spent(8) == "budget_steps"
    clock.now += 61
    trace.tokens = 50_000
    assert b.spent(8) == "budget_steps"
    other = budget.Budget.for_("analyst", deep=False, clock=clock)
    clock.now += 60
    assert other.spent(0) == "budget_duration"


def _state(spent: bool) -> engine.State:
    s = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        plan=p.Plan(goal="g", steps=[p.PlanStep(id="s1", tool="query_metric", instruction="i")]),
    )
    clock = Clock()
    s.budget = budget.Budget.for_("executive", deep=False, clock=clock)
    if spent:
        clock.now += 31
    return s


def test_a_spent_turn_composes_from_what_it_found_and_says_why(trace: turn_trace.Trace) -> None:
    assert engine.after_act(_state(spent=False)) == "act"
    s = _state(spent=True)
    assert engine.after_act(s) == "compose"
    assert engine.after_review(s) == "compose"
    assert trace.stop_reason == "budget_duration"


def test_the_run_records_the_budget_its_turn_ran_out_of() -> None:
    t = turn_trace.Trace(uuid.uuid4(), uuid.uuid4(), None)
    assert turn_trace.outcome(None, t, "answered") == ("succeeded", "completed")
    t.stop_reason = "budget_tokens"
    assert turn_trace.outcome(None, t, "answered") == ("succeeded", "budget_tokens")
    assert turn_trace.outcome(RuntimeError(), t, "answered") == ("failed", "error")
    t.cancelled = True
    assert turn_trace.outcome(None, t, "answered") == ("cancelled", "user_cancelled")
