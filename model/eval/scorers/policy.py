"""Policy-violation rate — offline, deterministic, no model involved.

Every case is put to the real defence it targets: sandbox_executor's sql_guard,
its AST validator, the tool-executor's path check, the staged-input name rule.
No re-implementation lives here, because a re-implementation is what the eval
would then be measuring.

Two rates come out of one corpus, and both are needed:

  * `policy_violation_rate` — must-refuse cases that got through. This is the
    number people mean by "policy violations", and on its own it is trivially
    satisfiable by refusing everything.
  * `false_refusal_rate` — must-allow cases that were refused. A guard that
    blocks ordinary questions is not safe, it is broken, and without this the
    gate would happily reward making it stricter until nothing works.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from model.eval import _paths

_paths.ensure(_paths.SANDBOX_EXECUTOR, _paths.WORKFLOW_RUNTIME)


class _Refused(Exception):
    """Normalised "the defence said no" across surfaces that each raise their own."""


def _sql_guard(case: dict[str, Any]) -> None:
    from sql_guard import SQLGuardError, validate_select_only

    try:
        validate_select_only(case["input"], case.get("dialect"))
    except SQLGuardError as e:
        raise _Refused(str(e)) from e


def _python_guard(case: dict[str, Any]) -> None:
    from security_visitor import SecurityError, validate_python_code

    try:
        validate_python_code(case["input"])
    except SecurityError as e:
        raise _Refused(str(e)) from e
    except SyntaxError as e:
        # main.py treats an unparseable submission as a validation failure too.
        raise _Refused(f"syntax error: {e}") from e


def _path_guard(case: dict[str, Any]) -> None:
    utils = _paths.load_module(
        "_eval_tool_executor_utils",
        _paths.WORKFLOW_RUNTIME / "nodes" / "sandbox" / "tool_executor" / "_utils.py",
    )

    _, error = utils._safe_path(case["input"], case.get("allowed_dir", ""))
    if error:
        raise _Refused(error)


def _sandbox_input_name(case: dict[str, Any]) -> None:
    from input_names import validate_input_name

    try:
        validate_input_name(case["input"])
    except ValueError as e:
        raise _Refused(str(e)) from e


SURFACES: dict[str, Callable[[dict[str, Any]], None]] = {
    "sql_guard": _sql_guard,
    "python_guard": _python_guard,
    "path_guard": _path_guard,
    "sandbox_input_name": _sandbox_input_name,
}


def score(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run every case through its surface and return metrics + per-case results."""
    results: list[dict[str, Any]] = []
    must_refuse = must_allow = violations = false_refusals = 0

    for case in cases:
        surface = case.get("surface", "")
        runner = SURFACES.get(surface)
        if runner is None:
            raise ValueError(f"case {case['id']}: unknown surface {surface!r}")

        refused_reason = ""
        try:
            runner(case)
            refused = False
        except _Refused as e:
            refused = True
            refused_reason = str(e)

        expected_refusal = case["expect"] == "refuse"
        passed = refused == expected_refusal
        if expected_refusal:
            must_refuse += 1
            violations += 0 if passed else 1
        else:
            must_allow += 1
            false_refusals += 0 if passed else 1

        results.append({
            "id": case["id"],
            "surface": surface,
            "tag": case.get("tag", ""),
            "expected": case["expect"],
            "actual": "refuse" if refused else "allow",
            "passed": passed,
            "reason": refused_reason,
        })

    return {
        "metrics": {
            "policy_violation_rate": (violations / must_refuse) if must_refuse else 0.0,
            "false_refusal_rate": (false_refusals / must_allow) if must_allow else 0.0,
        },
        "counts": {
            "must_refuse": must_refuse,
            "must_allow": must_allow,
            "violations": violations,
            "false_refusals": false_refusals,
        },
        "results": results,
    }
