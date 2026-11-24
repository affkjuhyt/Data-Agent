"""AE-18 (AGC-05): an Ask turn's run is written when the turn starts and each step as it ends, so
the steps done are readable while the turn still runs, and a turn that dies keeps them."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent import turn_trace

from .conftest import Seed
from .test_questions import ask

pytestmark = [pytest.mark.integration, pytest.mark.req("AE-18")]


def _run(seed: Seed, question: Any) -> Any:
    return seed.one(
        "SELECT id, status, finished_at FROM agent.agent_run WHERE trigger_ref = %s",
        f"question:{question}",
    )


def _steps(seed: Seed, run_id: Any) -> list[tuple[Any, ...]]:
    return seed.all(
        "SELECT seq, name, status FROM agent.agent_step WHERE run_id = %s ORDER BY seq", run_id
    )


async def test_the_steps_done_are_written_while_the_turn_runs(seed: Seed) -> None:
    asked = ask(seed)
    async with turn_trace.turn(asked.org, asked.question, asked.users[0]):
        run_id, status, finished = _run(seed, asked.question)
        assert (status, finished) == ("running", None)
        async with turn_trace.stepping("understand"):
            # The step under way is the stream's: nothing of it in the table yet.
            assert _steps(seed, run_id) == []
        assert _steps(seed, run_id) == [(1, "understand", "ok")]
        # A model call is written with the next step that ends.
        turn_trace.model_call(
            role="planner", model="m", input_tokens=10, output_tokens=5, latency_ms=3
        )
        await turn_trace.record_step("validate", status="failed", summary="Không khớp")
        assert [s[1:] for s in _steps(seed, run_id)] == [
            ("understand", "ok"),
            ("model_call", "ok"),
            ("validate", "error"),
        ]
        assert seed.one("SELECT tokens FROM agent.agent_run WHERE id = %s", run_id) == 15
    # The turn ended: the same run, finished, with no step written twice.
    again, _, finished = _run(seed, asked.question)
    assert again == run_id and finished is not None
    assert len(_steps(seed, run_id)) == 3


async def test_a_turn_that_fails_keeps_the_steps_it_finished(seed: Seed) -> None:
    asked = ask(seed)
    with pytest.raises(RuntimeError):
        async with turn_trace.turn(asked.org, asked.question, asked.users[0]):
            await turn_trace.record_step("understand")
            await turn_trace.begin("choose_metric")
            raise RuntimeError("the worker went away")
    run_id, status, _ = _run(seed, asked.question)
    assert status == "failed"
    # The open step ends with the turn, as it failed.
    assert [s[1:] for s in _steps(seed, run_id)] == [
        ("understand", "ok"),
        ("choose_metric", "error"),
    ]
