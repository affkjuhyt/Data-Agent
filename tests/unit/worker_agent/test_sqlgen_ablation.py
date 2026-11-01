"""The SQL expert's ablation (sqlgen/ablation.py, R1-09): how a variant is scored, and that a
variant writes only its own ways."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.sqlgen.ablation import VARIANTS, Outcome, close, summarize
from services.worker_agent.sqlgen.generate import METHODS, candidates
from services.worker_agent.sqlgen.schema import Linked

pytestmark = [pytest.mark.unit]


def outcome(decision: str, correct: bool, confidence: float) -> Outcome:
    return Outcome("i", "q", decision, confidence, 1.0 if correct else 2.0, 1.0, correct)


def test_a_variant_is_scored_on_every_item_and_on_what_it_answered() -> None:
    s = summarize(
        "all",
        [
            outcome("answer", True, 1.0),
            outcome("review", False, 0.5),
            outcome("ask", False, 0.33),
            outcome("unlinked", False, 0.0),
        ],
    )
    assert (s.items, s.answered, s.correct) == (4, 2, 1)
    assert (s.accuracy, s.precision) == (0.25, 0.5)
    assert (s.confidence_right, s.confidence_wrong) == (1.0, 0.5)


def test_close_is_half_a_percent_of_the_expected_value() -> None:
    assert close(100.4, 100.0) and not close(100.6, 100.0) and not close(None, 1.0)


def test_the_variants_are_all_three_ways_and_each_alone() -> None:
    assert VARIANTS["all"] == METHODS and all(VARIANTS[m] == (m,) for m in METHODS)


class Counting:
    def __init__(self) -> None:
        self.systems: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **kw: Any
    ) -> Any:
        self.systems.append(system)
        return Reply(output.model_validate({"sql": "SELECT 1"}), "m", 1, 1)


async def test_a_variant_asks_only_for_its_own_way_twice() -> None:
    model = Counting()
    written = await candidates(model, "q", Linked([], [], []), "postgres", [], ("divide",))
    assert [c.method for c in written] == ["divide", "divide"]
    assert len(model.systems) == 2
