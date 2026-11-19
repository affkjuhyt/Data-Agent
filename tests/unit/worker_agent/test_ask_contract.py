"""Request contract for ask semantic planner: whether a question needs a metric."""

from __future__ import annotations

import json
import pathlib

import pytest

from services.worker_agent.ask.contract import (
    NumericIntent,
    Purpose,
    RequestContract,
)

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).parents[3]
ASK_PLANNER_CASES = ROOT / "model" / "eval" / "cases" / "ask-planner.jsonl"


def test_an_overview_does_not_ask_for_a_metric() -> None:
    contract = RequestContract(Purpose.ORIENT, NumericIntent.NONE)
    assert contract.needs_metric_choice() is False


def test_a_required_number_with_no_metric_asks() -> None:
    contract = RequestContract(
        Purpose.ANSWER, NumericIntent.REQUIRED, missing_required_slots=("metric",)
    )
    assert contract.needs_metric_choice() is True


def test_ask_planner_eval_cases_match_contract() -> None:
    rows: list[dict[str, str]] = []
    with ASK_PLANNER_CASES.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    by_id = {row["id"]: row for row in rows}

    orient = by_id["orient-what-data"]
    assert orient["question"] == "Các dữ liệu ở đây là về gì?"
    assert orient["purpose"] == "orient"
    assert orient["numeric_intent"] == "none"

    resolved = by_id["resolved-revenue"]
    assert resolved["question"] == "Doanh thu năm 2013"
    assert resolved["purpose"] == "answer"
    assert resolved["numeric_intent"] == "required"

    purpose_values = {row["purpose"] for row in rows}
    assert purpose_values <= {p.value for p in Purpose}

    numeric_values = {row["numeric_intent"] for row in rows}
    assert numeric_values <= {n.value for n in NumericIntent}
