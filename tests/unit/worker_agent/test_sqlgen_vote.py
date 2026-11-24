"""AE-23 (CONFIDENCE_SPEC §4): the exploratory SQL vote counted by way of writing. Two samples of
one way that agree are one vote, not two; a way split between two groups gives each half; the
share k/total the meter shows does not change."""

from __future__ import annotations

from typing import Any

import pytest

from libs import calibration as cal
from services.worker_agent.sqlgen.vote import vote

pytestmark = [pytest.mark.unit]

A: list[list[Any]] = [["x", 1]]
B: list[list[Any]] = [["x", 2]]
C: list[list[Any]] = [["x", 3]]


def test_equal_weights_count_like_the_candidates_did() -> None:
    # Two samples per way and equal weights: a group's score is k/2, so the winner is the count
    # vote's. skeleton twice in A, examples and divide once each in B: 1 against ½ + ½, a tie
    # that goes to A, seen first.
    results = [A, A, B, None, B, None]
    methods = ["skeleton", "skeleton", "examples", "examples", "divide", "divide"]
    v = vote(results, methods=methods)
    assert v.winner == 0 and v.margin == 0.0
    # The share stays k of the candidates in the winning group, for the meter.
    assert v.confidence == pytest.approx(2 / 6) and v.decision == "ask"
    assert v.support == {"skeleton": 1.0, "examples": 0.0, "divide": 0.0}
    # Weighted, the two ways that agree outweigh the one way twice.
    weighted = vote(
        results, methods=methods, weights={"skeleton": 1.0, "examples": 1.5, "divide": 1.5}
    )
    assert weighted.winner == 2 and weighted.margin == pytest.approx((1.5 - 1.0) / 4.0)


def test_a_way_split_between_two_groups_gives_each_half() -> None:
    results = [A, B, A, A, A, A]
    methods = ["skeleton", "skeleton", "examples", "examples", "divide", "divide"]
    v = vote(results, methods=methods)
    assert v.winner == 0
    # A: ½ + 1 + 1, B: ½, of W = 3.
    assert v.margin == pytest.approx((2.5 - 0.5) / 3)
    assert v.support["skeleton"] == 0.5 and v.confidence == pytest.approx(5 / 6)


def test_the_weights_decide_between_ways_that_disagree() -> None:
    results = [A, A, B, B, C, C]
    methods = ["skeleton", "skeleton", "examples", "examples", "divide", "divide"]
    assert vote(results, methods=methods).winner == 0  # three-way tie: the first group
    v = vote(results, methods=methods, weights={"skeleton": 0.2, "examples": 1.5, "divide": 0.3})
    assert v.winner == 2 and v.margin == pytest.approx((1.5 - 0.3) / 2.0)


def test_a_failed_way_still_counts_in_the_whole_weight() -> None:
    results = [A, A, None, None, None, None]
    methods = ["skeleton", "skeleton", "examples", "examples", "divide", "divide"]
    v = vote(results, methods=methods)
    assert v.winner == 0 and v.margin == pytest.approx(1 / 3)


def test_weights_are_logit_of_accuracy_and_never_negative() -> None:
    assert cal.method_weight(26, 66) == cal.MIN_WEIGHT  # EV-19's divide: 39 %, under a coin
    assert cal.method_weight(9, 10) == pytest.approx(1.6094, abs=1e-4)  # a = 10/12
    # A way the weights do not know weighs what the known ones weigh on average.
    v = vote([A, B], methods=["new", "skeleton"], weights={"skeleton": 2.0})
    assert v.margin == 0.0 and v.winner == 0


def test_without_methods_the_vote_is_the_count_it_was() -> None:
    results = [A, A, A, B, B, None]
    v = vote(results)
    assert (v.winner, v.decision, v.confidence) == (0, "review", 0.5)
    assert v.margin == pytest.approx((3 - 2) / 6)


def test_sqlgen_vote_count_picks_the_largest_group_again(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [A, A, A, B, B, None]
    methods = ["skeleton", "skeleton", "examples", "examples", "divide", "divide"]
    weights = {"skeleton": 0.05, "examples": 0.05, "divide": 2.0}
    assert vote(results, methods=methods, weights=weights).winner == 3
    monkeypatch.setenv("SQLGEN_VOTE", "count")
    assert vote(results, methods=methods, weights=weights).winner == 0
