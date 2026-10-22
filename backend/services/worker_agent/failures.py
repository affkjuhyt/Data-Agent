"""Why a step failed, and what happens next (R0-11, 03 §10).

The legacy judge sorted a failed answer into error classes and routed each one deterministically;
it went with workflow_runtime. V3's agent runs fail at named points (understanding the goal,
compiling it, querying the source), so the class is read from there, never guessed by a model,
and the route is a table: a class either ends the run with the refusal code it maps to, asks the
person to restate the goal, or, for a source that may come back, is retried on the run's durable
timers. A class that keeps coming back past what its route allows stops the run: retrying the
same failure again would only repeat it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from libs.contracts.v1.errors import ErrorCode


class ErrorClass(StrEnum):
    DATA_MISSING_DIRECT = "DATA_MISSING_DIRECT"  # the metric's table, or its period, has no data
    DATA_MISSING_INDIRECT = "DATA_MISSING_INDIRECT"  # no join path to a dimension asked for
    PLAN_LOGIC = "PLAN_LOGIC"  # the definition or the request does not compile
    TOOL_GRAMMAR = "TOOL_GRAMMAR"  # the SQL the read-only guard refused
    INTENT_MISMATCH = "INTENT_MISMATCH"  # no metric, several, or a cut the space does not have
    MATERIALIZATION = "MATERIALIZATION"  # the result does not fit the step's output contract
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"  # nothing to ground an answer on
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"  # a timeout, a source or OpenBao down


@dataclass(frozen=True)
class Route:
    action: Literal["refuse", "clarify", "retry"]
    code: ErrorCode
    # How many times the class may come back in a row before the run stops.
    attempts: int = 1


ROUTES: dict[ErrorClass, Route] = {
    ErrorClass.DATA_MISSING_DIRECT: Route("refuse", ErrorCode.NO_DATA_FOR_PERIOD),
    ErrorClass.DATA_MISSING_INDIRECT: Route("refuse", ErrorCode.NO_JOIN_PATH),
    ErrorClass.PLAN_LOGIC: Route("refuse", ErrorCode.CONTRACT_MISMATCH),
    ErrorClass.TOOL_GRAMMAR: Route("refuse", ErrorCode.PLANNER_ERROR),
    ErrorClass.INTENT_MISMATCH: Route("clarify", ErrorCode.UNRESOLVED_ENTITY),
    ErrorClass.MATERIALIZATION: Route("refuse", ErrorCode.CONTRACT_MISMATCH),
    ErrorClass.EVIDENCE_INSUFFICIENT: Route("refuse", ErrorCode.INSUFFICIENT_EVIDENCE),
    # The first failure and one retry per wait of runs.RETRY_DELAYS (5, 15, 60 minutes).
    ErrorClass.SOURCE_UNAVAILABLE: Route("retry", ErrorCode.TOOL_UNAVAILABLE, attempts=4),
}

# The compiler's refusal codes (semantic/compiler.CompileError.code), as classes.
_COMPILE = {
    "NO_DATA_FOR_PERIOD": ErrorClass.DATA_MISSING_DIRECT,
    "NO_JOIN_PATH": ErrorClass.DATA_MISSING_INDIRECT,
    "UNRESOLVED_ENTITY": ErrorClass.INTENT_MISMATCH,
}


def of_compile(code: str) -> ErrorClass:
    return _COMPILE.get(code, ErrorClass.PLAN_LOGIC)


def of_query(*, transient: bool) -> ErrorClass:
    """A compiled query that did not succeed: the source may come back (runs.is_transient), or it
    fails the same way every time (a wrong query, a missing grant or credential)."""
    return ErrorClass.SOURCE_UNAVAILABLE if transient else ErrorClass.PLAN_LOGIC


def in_a_row(classes: Sequence[ErrorClass | str | None]) -> int:
    """How many times the last class came back without another in between."""
    if not classes or classes[-1] is None:
        return 0
    last, n = classes[-1], 0
    for c in reversed(classes):
        if c != last:
            break
        n += 1
    return n


def stops(classes: Sequence[ErrorClass | str | None]) -> bool:
    """Whether the run stops here: the last class came back more often than its route allows."""
    if not classes or classes[-1] is None:
        return False
    route = ROUTES[ErrorClass(classes[-1])]
    return route.action != "retry" or in_a_row(classes) >= route.attempts
