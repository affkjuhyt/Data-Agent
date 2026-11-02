"""The agent pass (understand/analyst.py) on the bank of test_answers.py: a question neither the
rules nor the one-shot reading place is worked out with the tools, compiled, run and answered with
the figure the SQL computed, never one the model wrote."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent import questions
from services.worker_agent.llm import ModelUnavailable, Reply
from services.worker_agent.understand.analyst import AgentTurn

from . import test_answers
from .conftest import Seed
from .test_answers import answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


class Planner:
    """Only the agent's turns are answered; the one-shot reading and the narrator are down."""

    def __init__(self, *turns: dict[str, Any]) -> None:
        self.turns = list(turns)
        self.calls: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], *, max_tokens: int = 800
    ) -> Any:
        self.calls.append(output.__name__)
        if output is not AgentTurn or not self.turns:
            raise ModelUnavailable("only the agent's turns are scripted")
        return Reply(
            output.model_validate(self.turns.pop(0)), model="planner", tokens=40, latency_ms=1
        )


async def test_the_agent_works_out_a_question_and_the_figure_is_the_sqls(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    planner = Planner(
        {"tool": "search_metrics", "args": {"text": "dư nợ"}},
        {"tool": "describe_metric", "args": {"metric": "DU_NO"}},
        {"tool": "propose", "args": {"metric": "DU_NO", "period": "2026-09"}},
    )
    monkeypatch.setattr(questions, "gateway", lambda: planner)
    q = bank.ask("Tổng tiền đang cho vay ra tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    # 30/09 without internal lending: 800 + 200 + 1000 + 900 + 100, from the warehouse.
    assert [float(f["value"]) for f in figures] == [3000.0]
    assert planner.calls[0] == "Reading" and planner.calls[1:4] == ["AgentTurn"] * 3
