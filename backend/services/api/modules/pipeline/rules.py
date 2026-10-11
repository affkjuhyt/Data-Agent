"""Pure rules of the pipeline module, kept apart from the routes so they are tested without a database."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

# BR-22: a step runs in the sandbox or the work schema and never writes to a source. The worker
# enforces it with a read-only connection; refusing write statements here tells the author early.
_WRITE = re.compile(
    r"\b(insert|update|delete|merge|upsert|truncate|drop|alter|create|grant|revoke|copy|call|execute)\b",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")


def writes_in_sql(code: str) -> list[str]:
    """The write keywords a SQL step uses outside comments and string literals."""
    bare = _STRING.sub("''", _COMMENT.sub(" ", code))
    return sorted({m.group(1).lower() for m in _WRITE.finditer(bare)})


_CRON_FIELD = re.compile(r"^[\d*/,\-A-Za-z?LW#]+$")


def valid_cron(expr: str) -> bool:
    """Five fields (minute hour day month weekday), the form Temporal schedules take."""
    fields = expr.split()
    return len(fields) == 5 and all(_CRON_FIELD.match(f) for f in fields)


def creates_cycle(
    graph: Mapping[uuid.UUID, Iterable[uuid.UUID]], node: uuid.UUID, depends_on: Iterable[uuid.UUID]
) -> bool:
    """True when `node` depending on `depends_on` closes a loop in the pipeline dependency graph."""
    stack = list(depends_on)
    seen: set[uuid.UUID] = set()
    while stack:
        current = stack.pop()
        if current == node:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return False


def _columns(contract: Any) -> dict[str, str]:
    if not isinstance(contract, dict):
        return {}
    out: dict[str, str] = {}
    for col in contract.get("columns") or []:
        if isinstance(col, dict) and "name" in col:
            out[str(col["name"])] = str(col.get("type", ""))
    return out


def contract_errors(contract: Any) -> list[str]:
    """A contract lists its columns, each with a name and a type (UC-06-06 step 4)."""
    if not isinstance(contract, dict):
        return ["contract is an object"]
    columns = contract.get("columns")
    if not isinstance(columns, list) or not columns:
        return ["contract.columns lists at least one column"]
    errors = []
    names: set[str] = set()
    for i, col in enumerate(columns):
        if not isinstance(col, dict) or not col.get("name") or not col.get("type"):
            errors.append(f"contract.columns[{i}] needs name and type")
            continue
        if col["name"] in names:
            errors.append(f"contract.columns[{i}]: duplicate column {col['name']!r}")
        names.add(col["name"])
    return errors


def breaking_changes(old: Any, new: Any) -> list[str]:
    """What in `new` breaks consumers of `old` (BR-60): a removed column or a changed type."""
    before, after = _columns(old), _columns(new)
    changes = [f"column {name!r} removed" for name in before if name not in after]
    changes += [
        f"column {name!r} type {before[name]} -> {after[name]}"
        for name in before
        if name in after and before[name] != after[name]
    ]
    return changes


def totals_mismatch(totals_check: Any) -> list[str]:
    """Money columns whose totals differ before and after the run (UC-06-01 E2).

    The worker writes {"matched": bool, "columns": [{"column", "matched", ...}]}."""
    if not isinstance(totals_check, dict):
        return []
    bad = [
        str(c.get("column", "?"))
        for c in totals_check.get("columns") or []
        if isinstance(c, dict) and c.get("matched") is False
    ]
    if not bad and totals_check.get("matched") is False:
        bad = ["?"]
    return bad
