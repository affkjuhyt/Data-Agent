"""Deterministic error-class routing for the workflow judge boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ERROR_CLASSES = {
    "DATA_MISSING_DIRECT",
    "DATA_MISSING_INDIRECT",
    "PLAN_LOGIC",
    "TOOL_GRAMMAR",
    "INTENT_MISMATCH",
    "MATERIALIZATION",
    "EVIDENCE_INSUFFICIENT",
}


@dataclass(frozen=True)
class ErrorRoute:
    target: str
    refusal_code: str | None = None


def plan_signature(tool_calls: list[dict[str, Any]] | None) -> str:
    """Stable signature used to detect a planner returning the same plan again."""
    return json.dumps(tool_calls or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def classify_phase1_error(state: dict[str, Any]) -> str | None:
    """Classify an empty retrieval result before the LLM judge runs."""
    tool_calls = state.get("tool_calls") or []
    if not tool_calls:
        return None
    if state.get("tool_errors") or state.get("db_error"):
        return "TOOL_GRAMMAR"
    types = {call.get("type") for call in tool_calls if isinstance(call, dict)}
    if "db_query" in types and not state.get("result_sets"):
        return "DATA_MISSING_DIRECT"
    if types & {"kb_search", "mcp", "tool"} and not (
        state.get("kb_chunks") or state.get("mcp_results") or state.get("tool_results")
        or state.get("result_sets")
    ):
        return "DATA_MISSING_INDIRECT"
    return None


def route_error_class(
    error_class: str | None,
    *,
    available: set[str],
    history: list[str] | None = None,
) -> ErrorRoute:
    """Map one class to one route; a repeated class always terminates."""
    if error_class not in ERROR_CLASSES:
        return ErrorRoute("end")
    if (history or []).count(error_class) >= 2:
        return ErrorRoute("end", "PLANNER_ERROR")
    if error_class == "DATA_MISSING_DIRECT":
        return ErrorRoute("refusal", "NO_DATA_FOR_PERIOD")
    if error_class == "DATA_MISSING_INDIRECT":
        return ErrorRoute("refusal", "NO_JOIN_PATH")
    if error_class == "INTENT_MISMATCH":
        return ErrorRoute("clarify")
    targets = {
        "PLAN_LOGIC": "planner",
        "TOOL_GRAMMAR": "tool_executor",
        "MATERIALIZATION": "responder",
        "EVIDENCE_INSUFFICIENT": "kb_search",
    }
    target = targets[error_class]
    return ErrorRoute(target if target in available else "end")
