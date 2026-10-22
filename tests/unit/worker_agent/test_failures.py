"""R0-11: each error class takes its route, and a class back past its route stops the run."""

from __future__ import annotations

import pytest

from libs.contracts.v1.errors import ErrorCode
from services.worker_agent import runs
from services.worker_agent.failures import ROUTES, ErrorClass, in_a_row, of_compile, of_query, stops

pytestmark = [pytest.mark.unit]


def test_every_class_has_a_route() -> None:
    assert set(ROUTES) == set(ErrorClass)


@pytest.mark.parametrize(
    ("error_class", "action", "code"),
    [
        (ErrorClass.DATA_MISSING_DIRECT, "refuse", ErrorCode.NO_DATA_FOR_PERIOD),
        (ErrorClass.DATA_MISSING_INDIRECT, "refuse", ErrorCode.NO_JOIN_PATH),
        (ErrorClass.PLAN_LOGIC, "refuse", ErrorCode.CONTRACT_MISMATCH),
        (ErrorClass.TOOL_GRAMMAR, "refuse", ErrorCode.PLANNER_ERROR),
        (ErrorClass.INTENT_MISMATCH, "clarify", ErrorCode.UNRESOLVED_ENTITY),
        (ErrorClass.MATERIALIZATION, "refuse", ErrorCode.CONTRACT_MISMATCH),
        (ErrorClass.EVIDENCE_INSUFFICIENT, "refuse", ErrorCode.INSUFFICIENT_EVIDENCE),
        (ErrorClass.SOURCE_UNAVAILABLE, "retry", ErrorCode.TOOL_UNAVAILABLE),
    ],
)
def test_each_class_takes_its_route(error_class: ErrorClass, action: str, code: ErrorCode) -> None:
    assert (ROUTES[error_class].action, ROUTES[error_class].code) == (action, code)


def test_the_compilers_codes_and_a_query_outcome_name_their_class() -> None:
    assert of_compile("NO_JOIN_PATH") is ErrorClass.DATA_MISSING_INDIRECT
    assert of_compile("NO_DATA_FOR_PERIOD") is ErrorClass.DATA_MISSING_DIRECT
    assert of_compile("UNRESOLVED_ENTITY") is ErrorClass.INTENT_MISMATCH
    assert of_compile("CONTRACT_MISMATCH") is ErrorClass.PLAN_LOGIC
    assert of_query(transient=True) is ErrorClass.SOURCE_UNAVAILABLE
    assert of_query(transient=False) is ErrorClass.PLAN_LOGIC


def test_a_class_that_is_not_retried_stops_at_once() -> None:
    assert stops([ErrorClass.PLAN_LOGIC])
    assert stops([ErrorClass.SOURCE_UNAVAILABLE, ErrorClass.INTENT_MISMATCH])


def test_a_passing_failure_is_retried_until_it_comes_back_once_too_often() -> None:
    unavailable = ErrorClass.SOURCE_UNAVAILABLE
    allowed = ROUTES[unavailable].attempts
    # One wait per retry: the route and the run's timers agree.
    assert allowed == len(runs.RETRY_DELAYS) + 1
    assert [stops([unavailable] * n) for n in range(1, allowed + 1)] == [False] * (allowed - 1) + [
        True
    ]
    assert in_a_row([ErrorClass.PLAN_LOGIC, unavailable, unavailable]) == 2
    assert not stops([None])
