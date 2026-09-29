"""Refuse a contract change that does not bump its definition version (P0-09 AC-2, BR-4).

    python -m services.indicator_api.contract_check --baseline <openapi.json of the base branch>

The baseline must come from the branch being merged into, not from the PR
itself: a PR that edits a field and regenerates openapi.json would otherwise
compare the new definition with itself and pass. Only schemas that carry an
`x-schema-version` (the contract models) are checked; bumping it is the
reviewable act of approving the change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

VERSION_KEY = "x-schema-version"


def _schemas(openapi: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = openapi.get("components", {}).get("schemas", {})
    return schemas


def _without_version(schema: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in schema.items() if k != VERSION_KEY}


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    changed = sorted(
        name
        for name in old_props.keys() | new_props.keys()
        if old_props.get(name) != new_props.get(name)
    )
    if sorted(old.get("required", [])) != sorted(new.get("required", [])):
        changed.append("(required fields)")
    return changed or ["(schema)"]


def contract_violations(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """One message per versioned schema that changed, or disappeared, without a version bump."""
    violations: list[str] = []
    current_schemas = _schemas(current)
    for name, old in sorted(_schemas(baseline).items()):
        if VERSION_KEY not in old:
            continue
        new = current_schemas.get(name)
        if new is None:
            violations.append(f"{name}: removed from the contract")
            continue
        if _without_version(old) == _without_version(new):
            continue
        old_version, new_version = old[VERSION_KEY], new.get(VERSION_KEY, 0)
        if new_version <= old_version:
            fields = ", ".join(_changed_fields(old, new))
            violations.append(
                f"{name}: {fields} changed but {VERSION_KEY} is still {new_version} "
                f"(was {old_version}); bump SCHEMA_VERSION"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    from services.indicator_api.main import create_app

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    violations = contract_violations(baseline, create_app().openapi())
    for line in violations:
        print(f"CONTRACT: {line}", file=sys.stderr)
    if violations:
        return 1
    print("contract: every changed definition carries a new version")
    return 0


if __name__ == "__main__":
    sys.exit(main())
