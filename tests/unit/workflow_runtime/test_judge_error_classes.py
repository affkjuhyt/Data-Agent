from __future__ import annotations

import pytest

from services.workflow_runtime.application.judge_routing import (
    ERROR_CLASSES,
    classify_phase1_error,
    plan_signature,
    route_error_class,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error_class", "target", "refusal_code"),
    [
        ("DATA_MISSING_DIRECT", "refusal", "NO_DATA_FOR_PERIOD"),
        ("DATA_MISSING_INDIRECT", "refusal", "NO_JOIN_PATH"),
        ("PLAN_LOGIC", "planner", None),
        ("TOOL_GRAMMAR", "tool_executor", None),
        ("INTENT_MISMATCH", "clarify", None),
        ("MATERIALIZATION", "responder", None),
        ("EVIDENCE_INSUFFICIENT", "kb_search", None),
    ],
)
def test_each_error_class_has_one_deterministic_route(error_class, target, refusal_code) -> None:
    decision = route_error_class(error_class, available={"planner", "tool_executor", "responder", "kb_search"})

    assert decision.target == target
    assert decision.refusal_code == refusal_code


def test_repeated_error_class_stops_instead_of_looping() -> None:
    decision = route_error_class(
        "PLAN_LOGIC",
        available={"planner", "responder"},
        history=["PLAN_LOGIC", "PLAN_LOGIC"],
    )

    assert decision.target == "end"
    assert decision.refusal_code == "PLANNER_ERROR"


def test_phase1_distinguishes_direct_data_query_from_indirect_retrieval() -> None:
    assert classify_phase1_error({"tool_calls": [{"type": "db_query"}], "result_sets": []}) == "DATA_MISSING_DIRECT"
    assert classify_phase1_error({"tool_calls": [{"type": "kb_search"}], "kb_chunks": []}) == "DATA_MISSING_INDIRECT"
    assert classify_phase1_error({"tool_calls": [], "kb_chunks": []}) is None


def test_plan_signature_is_stable_for_equivalent_tool_calls() -> None:
    left = [{"type": "db_query", "query": "select 1", "arguments": {"b": 2, "a": 1}}]
    right = [{"arguments": {"a": 1, "b": 2}, "query": "select 1", "type": "db_query"}]

    assert plan_signature(left) == plan_signature(right)
    assert {
        "DATA_MISSING_DIRECT", "DATA_MISSING_INDIRECT", "PLAN_LOGIC", "TOOL_GRAMMAR",
        "INTENT_MISMATCH", "MATERIALIZATION", "EVIDENCE_INSUFFICIENT",
    } == ERROR_CLASSES
