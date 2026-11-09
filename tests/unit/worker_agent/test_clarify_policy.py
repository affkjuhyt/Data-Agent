"""Clarify policy (OP-1): audience budget, option cap, no second ask after the round limit."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import clarify_policy as cp
from services.worker_agent.agentic import plan as p

pytestmark = pytest.mark.unit


def _ask(*, options: list[str] | None = None, steps: list[p.PlanStep] | None = None) -> p.Plan:
    return p.Plan(
        goal="g",
        thought="t",
        mode="ask",
        clarification="Bạn hỏi doanh thu Internet hay Reseller?",
        options=options
        or [
            "Internet Sales",
            "Reseller Sales",
            "Cả hai",
            "Theo vùng",
            "Theo tháng",
            "Theo năm",
        ],
        steps=steps or [],
    )


def test_executive_keeps_one_ask_and_caps_options() -> None:
    out = cp.apply(_ask(), audience="executive", prior_rounds=0)
    assert out.mode == "ask"
    assert out.clarification
    assert len(out.options) == 3


def test_analyst_allows_three_rounds_and_five_options() -> None:
    out = cp.apply(_ask(), audience="analyst", prior_rounds=2)
    assert out.mode == "ask"
    assert len(out.options) == 5


def test_after_budget_ask_becomes_analyze_with_assumption() -> None:
    step = p.PlanStep(id="s1", tool="query_metric", instruction="đọc doanh thu")
    out = cp.apply(_ask(steps=[step]), audience="executive", prior_rounds=1)
    assert out.mode == "analyze"
    assert out.clarification is None
    assert out.options == []
    assert "Giả định hợp lý" in out.thought


def test_after_budget_without_steps_becomes_answer() -> None:
    out = cp.apply(_ask(), audience="analyst", prior_rounds=3)
    assert out.mode == "answer"
    assert out.clarification is None


def test_ask_without_clarification_text_is_demoted_to_answer() -> None:
    plan = p.Plan(goal="g", mode="ask", clarification="  ", options=["a"])
    out = cp.apply(plan, audience="analyst", prior_rounds=0)
    assert out.mode == "answer"
    assert out.options == []


def test_analyze_and_answer_pass_through() -> None:
    analyze = p.Plan(
        goal="g",
        mode="analyze",
        steps=[p.PlanStep(id="s1", tool="query_metric", instruction="x")],
    )
    assert cp.apply(analyze, prior_rounds=9) is analyze
    answer = p.Plan(goal="g", mode="answer")
    assert cp.apply(answer, prior_rounds=9) is answer
