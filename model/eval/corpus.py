"""Loading and validating the evaluation corpus.

The cases are versioned data, not fixtures generated at run time: a metric is
only comparable across releases if the questions behind it did not change
underneath it. They live as JSONL so a diff shows exactly which case was added,
edited or removed, and so a reviewer can argue with an individual line.

Every case carries an `id` because the reports key on it — "sql-011 regressed"
is a sentence someone can act on; "3 of 25 failed" is not.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).parent / "cases"


class CorpusError(Exception):
    """A malformed corpus. Always fatal: a gate reading a broken dataset would
    report a score for something other than what the dataset says."""


def load_cases(name: str) -> list[dict[str, Any]]:
    """Every case in cases/{name}.jsonl, in file order.

    Blank lines are skipped so the file can be grouped visually; anything else
    that fails to parse is an error rather than a silently dropped case — a
    corpus that quietly shrinks makes every rate look better than it is.
    """
    path = CASES_DIR / f"{name}.jsonl"
    if not path.exists():
        raise CorpusError(f"missing case file: {path}")

    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as e:
            raise CorpusError(f"{path.name}:{lineno} is not valid JSON: {e}") from e
        if not isinstance(case, dict):
            raise CorpusError(f"{path.name}:{lineno} is not an object")
        case_id = case.get("id")
        if not case_id:
            raise CorpusError(f"{path.name}:{lineno} has no id")
        if case_id in seen:
            raise CorpusError(f"{path.name}:{lineno} duplicate id {case_id!r}")
        seen.add(str(case_id))
        cases.append(case)

    if not cases:
        raise CorpusError(f"{path.name} is empty")
    return cases


def load_catalogs() -> dict[str, Any]:
    """Metric contracts the SQL cases are written against."""
    path = CASES_DIR / "catalogs.json"
    if not path.exists():
        raise CorpusError(f"missing catalog file: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}
