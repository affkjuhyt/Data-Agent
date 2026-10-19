"""How an eval item is scored against the answer the QA handler gave (eval_runs._score)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.worker_agent.eval_runs import _score

pytestmark = [pytest.mark.unit]


def figure(value: str | None, *, derived: bool = False) -> dict[str, Any]:
    # As Figure.as_json stores it: the value is text, a derived figure's source is an expression.
    source = {"compute_run_id": "c-1", "expr": "F1 + F2"} if derived else {"query_run_id": "q-1"}
    return {"key": "F1", "value": value, "source": source}


def item(expected: dict[str, Any], tolerance: float | None = None) -> Any:
    return SimpleNamespace(expected=expected, category="value", tolerance=tolerance)


def answer(*figures: dict[str, Any], kind: str = "data", text: str = "") -> Any:
    return SimpleNamespace(kind=kind, figures=list(figures), text=text)


QUESTION = SimpleNamespace(status="answered")


def test_a_value_stored_as_text_is_scored_and_derived_figures_are_not_counted() -> None:
    got = answer(figure("2000.00"), figure("1000.00"), figure("3000.00", derived=True))
    understanding, value, behavior, passed, _, cause = _score(
        item({"kind": "data", "values": [2000, 1000]}), got, QUESTION
    )
    assert (understanding, value, behavior, passed, cause) == (True, True, True, True, None)


def test_a_wrong_value_fails_and_a_tolerance_lets_a_close_one_pass() -> None:
    got = answer(figure("1.84"))
    assert _score(item({"values": [1.80]}), got, QUESTION)[3] is False
    assert _score(item({"values": [1.80]}, tolerance=0.05), got, QUESTION)[3] is True


def test_a_masked_figure_is_not_a_value() -> None:
    assert _score(item({"values": [5]}), answer(figure(None)), QUESTION)[1] is False
