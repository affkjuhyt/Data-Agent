"""The SQL expert's ablation (sqlgen/ablation.py, R1-09): how a variant is scored, and that a
variant writes only its own ways."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.query.executor import QueryOutcome
from services.worker_agent.sqlgen import run as sql_run
from services.worker_agent.sqlgen.ablation import (
    VARIANTS,
    Outcome,
    _number,
    close,
    decision_of,
    outcome_of,
    summarize,
)
from services.worker_agent.sqlgen.generate import METHODS, Candidate, candidates
from services.worker_agent.sqlgen.run import Exploration
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


def test_a_variant_is_judged_on_its_share_of_the_vote() -> None:
    # Two candidates that agree are as sure as six that do; the production bar is 4/6 and 3/6.
    assert decision_of("ask", 1.0) == "answer"
    assert decision_of("ask", 0.5) == "review"
    assert decision_of("answer", 4 / 6) == "answer" and decision_of("ask", 2 / 6) == "ask"
    assert decision_of("failed", 0.0) == "failed"


# EV-19: what calibrating the vote needs, CONFIDENCE_SPEC §3-4.


def _ran(value: float) -> QueryOutcome:
    return QueryOutcome(uuid.uuid4(), "succeeded", columns=[{"name": "sum"}], rows=[[value]])


def test_explore_numbers_the_groups_the_candidates_fell_in(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"SELECT 1": 100.0, "SELECT 2": 100.0, "SELECT 3": 90.0}

    async def fake_candidates(*_a: Any, **_k: Any) -> list[Candidate]:
        return [
            Candidate("skeleton", "SELECT 3"),
            Candidate("skeleton", "SELECT 1"),
            Candidate("examples", "SELECT 2"),
            Candidate("examples", "SELECT bad"),
            Candidate("divide", "SELECT 1"),
            Candidate("divide", "SELECT 3"),
        ]

    async def run_sql(sql: str) -> QueryOutcome:
        return _ran(values[sql])

    monkeypatch.setattr(sql_run, "candidates", fake_candidates)
    monkeypatch.setattr(sql_run, "problems", lambda sql, *_a, **_k: ["bad"] if "bad" in sql else [])

    async def no_repair(*_a: Any, **_k: Any) -> Candidate:
        return Candidate("examples", None, error="no SQL")

    monkeypatch.setattr(sql_run, "repair", no_repair)
    found = asyncio.run(
        sql_run.explore(None, "q", Linked([], [], []), "postgres", [], run_sql)  # type: ignore[arg-type]
    )
    # Group 0 is the first result met (90), group 1 the next (100); a rejected one has none.
    assert [t.get("group") for t in found.tried] == [0, 1, 1, None, 1, 0]
    assert (found.agreeing, found.decision) == (3, "review")
    assert [o is not None for o in found.outcomes] == [True, True, True, False, True, True]


def _exploration(decision: str, confidence: float, winner: float | None) -> Exploration:
    tried = [
        {"method": "skeleton", "status": "ran", "group": 0},
        {"method": "examples", "status": "ran", "group": 1},
        {"method": "divide", "status": "rejected"},
    ]
    e = Exploration(
        decision,  # type: ignore[arg-type]
        confidence,
        round(confidence * 3),
        tried=tried,
        outcomes=[_ran(100.0), _ran(90.0), None],
    )
    if winner is not None:
        e.sql, e.columns, e.rows = "SELECT 1", ["sum"], [[winner]]
    return e


def test_the_winner_is_scored_whatever_the_vote_decided() -> None:
    # A one-of-three share is not answered, but whether its winner was right is what tells the
    # calibration how often such a share is right: scoring it wrong would be by construction.
    o = outcome_of("i", "q", 100.0, _exploration("ask", 1 / 3, 100.0))
    assert (o.decision, o.correct, o.value, o.winner_correct) == ("ask", False, None, True)
    assert o.candidates == [
        {"method": "skeleton", "status": "ran", "group": 0, "value": 100.0, "correct": True},
        {"method": "examples", "status": "ran", "group": 1, "value": 90.0, "correct": False},
        {"method": "divide", "status": "rejected", "group": None, "value": None, "correct": False},
    ]


def test_an_answered_item_keeps_its_scoring_and_no_winner_is_none() -> None:
    o = outcome_of("i", "q", 100.0, _exploration("answer", 1.0, 100.2))
    assert (o.decision, o.correct, o.value, o.winner_correct) == ("answer", True, 100.2, True)
    assert outcome_of("i", "q", 100.0, _exploration("failed", 0.0, None)).winner_correct is None


def test_the_vote_margin_is_kept_with_the_winner() -> None:
    e = _exploration("ask", 1 / 3, 100.0)
    e.margin = 0.25
    assert outcome_of("i", "q", 100.0, e).margin == 0.25
    assert outcome_of("i", "q", 100.0, _exploration("failed", 0.0, None)).margin is None


def test_only_items_expecting_a_figure_are_number_items() -> None:
    # DS-TLC also asks for a zone by name: one expected value, not a figure.
    assert _number("12.5") and _number(3) and not _number("Midtown Center") and not _number(None)
