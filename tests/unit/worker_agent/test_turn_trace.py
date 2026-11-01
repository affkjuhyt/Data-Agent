"""The turn trace collects steps and content-free model calls (turn_trace.py)."""

from __future__ import annotations

import decimal
import uuid

import pytest

from services.worker_agent import turn_trace

pytestmark = [pytest.mark.unit]


@pytest.fixture
def trace() -> turn_trace.Trace:
    collected = turn_trace.Trace(org_id=uuid.uuid4(), question_id=uuid.uuid4(), user_id=None)
    token = turn_trace._current.set(collected)
    yield collected
    turn_trace._current.reset(token)


def test_nothing_is_recorded_outside_a_turn() -> None:
    turn_trace.step("understand", "running")
    turn_trace.model_call(role="planner", model="m", input_tokens=5, output_tokens=5, latency_ms=1)
    assert turn_trace._current.get() is None


def test_steps_open_and_close_with_their_kind(trace: turn_trace.Trace) -> None:
    turn_trace.step("understand", "running")
    turn_trace.step("understand", "done")
    turn_trace.step("query", "running")
    turn_trace.step("query", "failed", detail="nguồn không trả lời")
    turn_trace.step("visualize", "done")
    assert [(s.kind, s.name, s.status) for s in trace.steps] == [
        ("plan", "understand", "ok"),
        ("query", "query", "error"),
        ("plan", "visualize", "ok"),
    ]
    assert trace.steps[1].summary == "nguồn không trả lời"
    assert all(s.duration_ms is not None for s in trace.steps)


def test_a_model_call_keeps_tokens_and_latency_but_no_content(
    trace: turn_trace.Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PRICES", '{"responder": {"input_per_1k": 0.5, "output_per_1k": 2}}')
    turn_trace._prices.cache_clear()
    turn_trace.model_call(
        role="responder", model="qwen", input_tokens=1000, output_tokens=500, latency_ms=840
    )
    turn_trace.model_call(
        role="planner",
        model="planner",
        input_tokens=0,
        output_tokens=0,
        latency_ms=20,
        error="ModelHTTPError: 503",
    )
    turn_trace._prices.cache_clear()
    answered, failed = trace.steps
    assert (answered.kind, answered.name, answered.status) == ("llm_call", "responder", "ok")
    assert answered.output == {
        "model": "qwen",
        "input_tokens": 1000,
        "output_tokens": 500,
        "latency_ms": 840,
    }
    assert answered.cost == decimal.Decimal("1.5000")
    assert (failed.status, failed.summary, failed.cost) == ("error", "ModelHTTPError: 503", 0)
    assert trace.tokens == 1500 and trace.cost == decimal.Decimal("1.5000")


def test_an_unpriced_model_costs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PRICES", raising=False)
    turn_trace._prices.cache_clear()
    assert turn_trace.price("qwen", "responder", 10_000, 10_000) == 0
    turn_trace._prices.cache_clear()
