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
    turn_trace.model_call(role="planner", model="m", input_tokens=5, output_tokens=5, latency_ms=1)
    assert turn_trace._current.get() is None


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    events: list[dict] = []

    async def publish(question_id: str, event: str, data: dict) -> None:
        events.append({"question_id": question_id, "event": event, **data})

    monkeypatch.setattr(turn_trace, "publish", publish)
    return events


async def test_stepping_publishes_running_then_done_with_detail(
    trace: turn_trace.Trace, published: list[dict]
) -> None:
    async with turn_trace.stepping("query", target="AdventureWorksDW") as s:
        s.detail = {"row_count": 3}
    running, done = published
    assert (running["event"], running["kind"], running["status"]) == ("activity", "step", "running")
    assert running["detail"] is None and running["title"] == "Truy vấn" and running["seq"] == 1
    assert (done["status"], done["detail"], done["step_id"]) == (
        "done",
        {"row_count": 3},
        s.step_id,
    )
    assert isinstance(done["duration_ms"], int)
    assert trace.steps[0].kind == "query" and trace.steps[0].name == "query"
    assert trace.steps[0].output == {
        "detail": {"row_count": 3},
        "_view": {
            "step_id": s.step_id,
            "title": "Truy vấn",
            "target": "AdventureWorksDW",
            "chips": [],
            "parent": None,
        },
    }


async def test_an_exception_fails_the_step_and_propagates(
    trace: turn_trace.Trace, published: list[dict]
) -> None:
    with pytest.raises(RuntimeError):
        async with turn_trace.stepping("query"):
            raise RuntimeError("source down")
    assert published[-1]["status"] == "failed"
    assert trace.steps[0].status == "error"


async def test_a_model_call_nests_under_the_open_step(
    trace: turn_trace.Trace, published: list[dict]
) -> None:
    turn_trace.model_call(role="planner", model="p", input_tokens=1, output_tokens=1, latency_ms=1)
    async with turn_trace.stepping("write_answer") as s:
        turn_trace.model_call(
            role="responder", model="r", input_tokens=1, output_tokens=1, latency_ms=1
        )
    outside, answer, inside = trace.steps
    assert outside.name == "model_call" and outside.output["_view"]["parent"] is None
    assert inside.name == "model_call" and inside.output["_view"]["parent"] == s.step_id
    assert inside.kind == "llm_call" and answer.name == "write_answer"
    # Model calls are not published: the step they belong to is.
    assert {e["action"] for e in published} == {"write_answer"}


async def test_stepping_outside_a_turn_publishes_nothing(published: list[dict]) -> None:
    async with turn_trace.stepping("query") as s:
        s.detail = {"x": 1}
    await turn_trace.record_step("validate")
    assert published == []


async def test_record_step_is_one_finished_event(
    trace: turn_trace.Trace, published: list[dict]
) -> None:
    await turn_trace.record_step(
        "ask_user", target="Bạn muốn hỏi chỉ số nào?", detail={"options": ["A"]}
    )
    [event] = published
    assert (event["action"], event["status"], event["title"]) == ("ask_user", "done", "Hỏi lại bạn")
    assert trace.steps[0].kind == "checkpoint"


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
    assert (answered.kind, answered.name, answered.status) == ("llm_call", "model_call", "ok")
    assert answered.output["detail"] == {
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
