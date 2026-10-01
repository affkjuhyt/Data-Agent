"""Fixture pairs for the contract compatibility gate (NOP-152).

Each case is the published `Observation` schema before (base) and after (head)
one edit, and `expected.json` says what the gate must decide. NOP-150 only
ships the pairs; the checker and the CI job that run them are NOP-152.

    python -m tests.unit.contracts.compat_fixtures   # rewrite after Observation changes
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from libs.contracts.schema_export import render_openapi

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPAT_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts" / "compat"

EXPECTED = {
    # Observation.unit changes type and x-schema-version stays 1: must be blocked.
    "breaking_without_bump": "blocked",
    # The same change with the version bumped to 2: allowed.
    "breaking_with_bump": "pass",
    # A new optional field, no bump: backward compatible, allowed.
    "compatible_optional_field": "pass",
}


def _document(observation: dict[str, Any]) -> dict[str, Any]:
    return {"openapi": "3.1.0", "components": {"schemas": {"Observation": observation}}}


def _dump(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render() -> dict[Path, str]:
    observation = json.loads(render_openapi())["components"]["schemas"]["Observation"]

    unit_changed = copy.deepcopy(observation)
    unit_changed["properties"]["unit"] = {"type": "object", "title": "Unit"}

    bumped = copy.deepcopy(unit_changed)
    bumped["x-schema-version"] = observation["x-schema-version"] + 1

    optional_added = copy.deepcopy(observation)
    optional_added["properties"]["note"] = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "title": "Note",
    }

    heads = {
        "breaking_without_bump": unit_changed,
        "breaking_with_bump": bumped,
        "compatible_optional_field": optional_added,
    }
    files = {
        COMPAT_DIR / "base.openapi.json": _dump(_document(observation)),
        COMPAT_DIR / "expected.json": _dump(EXPECTED),
    }
    for case, head in heads.items():
        files[COMPAT_DIR / case / "head.openapi.json"] = _dump(_document(head))
    return files


if __name__ == "__main__":
    for path, text in render().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
