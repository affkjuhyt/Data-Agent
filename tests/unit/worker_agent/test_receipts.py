"""AE-05: the receipt rows of figures read from query runs (explore, verified query, agentic)."""

from __future__ import annotations

import datetime
import decimal
import uuid

import pytest

from services.worker_agent import receipts, turn_trace

pytestmark = [pytest.mark.unit]

RUN = uuid.uuid4()
RUNS = {RUN: receipts.Run("a" * 64, "b" * 64, datetime.date(2013, 12, 31))}


def test_a_figure_is_sealed_from_its_query_run() -> None:
    (row,) = receipts.query_figures(
        [
            {
                "key": "F1",
                "label": "Doanh thu",
                "value": "12.50",
                "unit": "USD",
                "source": {"query_run_id": str(RUN), "row": 0, "col": "value"},
            }
        ],
        RUNS,
    )
    assert row == {
        "figure_key": "F1",
        "label": "Doanh thu",
        "value": decimal.Decimal("12.50"),
        "unit": "USD",
        "display_state": "shown",
        "source_kind": "query_run",
        "query_run_id": RUN,
        "compiled_sql_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "as_of": datetime.date(2013, 12, 31),
        "freshness_state": "not_applicable",
    }


def test_an_empty_cell_is_not_available_and_missing() -> None:
    # D4: a shown figure has a value; not_available goes with freshness `missing`.
    (row,) = receipts.query_figures(
        [{"key": "F1", "label": "x", "value": None, "query_run_id": str(RUN)}], RUNS
    )
    assert (row["value"], row["display_state"], row["freshness_state"]) == (
        None,
        "not_available",
        "missing",
    )


def test_only_what_can_be_replayed_is_sealed_once() -> None:
    figures = [
        {"key": "F1", "label": "a", "value": "1", "query_run_id": str(RUN)},
        # The same key again (one figure per key, UNIQUE in D4).
        {"key": "F1", "label": "a", "value": "2", "query_run_id": str(RUN)},
        # No run, a run nobody knows, a run id that is not one.
        {"key": "F2", "label": "b", "value": "1"},
        {"key": "F3", "label": "c", "value": "1", "query_run_id": str(uuid.uuid4())},
        {"key": "F4", "label": "d", "value": "1", "query_run_id": "not-a-run"},
        # A value that is not a number states none.
        {"key": "F5", "label": "e", "value": "NaN", "query_run_id": str(RUN)},
    ]
    rows = receipts.query_figures(figures, RUNS)
    assert [(r["figure_key"], r["value"]) for r in rows] == [
        ("F1", decimal.Decimal("1")),
        ("F5", None),
    ]


def test_a_turns_model_calls_are_listed_for_its_receipt() -> None:
    assert turn_trace.models_called() == []
    trace = turn_trace.Trace(org_id=uuid.uuid4(), question_id=uuid.uuid4(), user_id=None)
    token = turn_trace._current.set(trace)
    try:
        turn_trace.model_call(
            role="planner", model="qwen", input_tokens=10, output_tokens=5, latency_ms=1
        )
        assert turn_trace.models_called() == [
            {"task": "planner", "model_name": "qwen", "tokens": 15}
        ]
    finally:
        turn_trace._current.reset(token)
