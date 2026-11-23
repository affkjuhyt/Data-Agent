"""AE-20 (AUD-11): a plan is validated before any of its steps runs, wherever it came from: the
tool is in the registry, the DAG has no cycle, the arguments the planner wrote fit the tool's
ports, the steps still to run fit the budget. A failure is a structured PLANNER_ERROR."""

from __future__ import annotations

import asyncio
import types
import uuid
from typing import Any

import pytest

from services.worker_agent.agentic import engine
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.workspace import Workspace

pytestmark = [pytest.mark.unit, pytest.mark.req("AE-20")]


def _plan(*steps: dict[str, Any]) -> p.Plan:
    return p.Plan(goal="g", steps=[p.PlanStep(**s) for s in steps])


def test_an_argument_of_the_wrong_type_is_refused_and_a_missing_one_is_the_executors() -> None:
    registry = engine._registry()
    with pytest.raises(p.PlanInvalid) as refused:
        p.validate(
            _plan(
                {"id": "s1", "tool": "query_metric", "instruction": "x", "args": {"group_by": 3}}
            ),
            registry,
        )
    assert refused.value.error() == {
        "code": "PLANNER_ERROR",
        "check": "ports",
        "step": "s1",
        "reason": str(refused.value),
    }
    # Only the metric known yet: the executor writes the rest from the step.
    fine = _plan({"id": "s1", "tool": "query_metric", "instruction": "x", "args": {"metric": "M"}})
    assert p.validate(fine, registry) is fine


def test_the_steps_still_to_run_must_fit_the_budget() -> None:
    steps = [
        {"id": "s1", "tool": "query_metric", "instruction": "x", "status": "done"},
        {"id": "s2", "tool": "query_metric", "instruction": "y"},
        {"id": "s3", "tool": "search_catalog", "instruction": "z", "depends_on": ["s2"]},
    ]
    names = set(engine.TOOLS)
    # The step already done is not counted.
    assert p.validate(_plan(*steps), names, steps_left=2)
    with pytest.raises(p.PlanInvalid) as over:
        p.validate(_plan(*steps), names, steps_left=1)
    assert over.value.check == "cost" and "ngân sách" in str(over.value)


def test_capability_and_dag_failures_say_which_check_and_step() -> None:
    names = set(engine.TOOLS)
    with pytest.raises(p.PlanInvalid) as unknown:
        p.validate(_plan({"id": "s1", "tool": "drop_table", "instruction": "x"}), names)
    assert (unknown.value.check, unknown.value.step) == ("capability", "s1")
    with pytest.raises(p.PlanInvalid) as forward:
        p.validate(
            _plan(
                {"id": "s1", "tool": "search_catalog", "instruction": "x", "depends_on": ["s2"]},
                {"id": "s2", "tool": "query_metric", "instruction": "y"},
            ),
            names,
        )
    assert (forward.value.check, forward.value.step) == ("dag", "s1")


def _state(plan: p.Plan, *, replans: int = 0) -> engine.State:
    state = engine.State(
        event=types.SimpleNamespace(),  # type: ignore[arg-type]
        question_id=uuid.uuid4(),
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ctx=types.SimpleNamespace(ws=Workspace(), question="Doanh thu?"),  # type: ignore[arg-type]
        plan=plan,
    )
    state.replans = replans
    return state


def _bad() -> p.Plan:
    # As the review could add it: a step whose arguments do not fit its tool.
    return _plan(
        {"id": "s1", "tool": "query_metric", "instruction": "x", "status": "done"},
        {"id": "s2", "tool": "query_metric", "instruction": "y", "args": {"group_by": 3}},
    )


def test_act_runs_nothing_of_a_plan_that_does_not_validate_and_asks_the_planner_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def never(s: Any, step: p.PlanStep) -> Any:
        ran.append(step.id)
        raise AssertionError("a step ran")

    monkeypatch.setattr(engine, "_run_step", never)
    state = _state(_bad())
    asyncio.run(engine.act(state))
    assert ran == [] and state.step_runs == 0
    assert state.feedback is not None and state.feedback.startswith("PLANNER_ERROR (ports)")
    assert engine.after_act(state) == "replan"


def test_once_replans_are_spent_the_invalid_steps_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_run_step", None)
    state = _state(_bad(), replans=engine.MAX_REPLANS)
    asyncio.run(engine.act(state))
    assert state.feedback is None
    skipped = state.plan.steps[1]  # type: ignore[union-attr]
    assert skipped.status == "skipped" and skipped.observation.startswith("PLANNER_ERROR")
    assert engine.after_act(state) == "review"
