#!/usr/bin/env python3
"""Small, dependency-free hooks shared by Claude and Codex-compatible runners."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # Preserve the leading status column from `git status --short`.
    return result.stdout.rstrip()


def changed_files() -> list[str]:
    output = run("git", "status", "--short")
    return [line[3:] for line in output.splitlines() if len(line) > 3]


def context() -> str:
    files = changed_files()
    if not files:
        return (
            "Data-Agent repo: read AGENTS.md before changing files. "
            "Use make test-changed while iterating; do not run host Python tests."
        )
    groups = ", ".join(sorted({path.split("/", 1)[0] for path in files}))
    return (
        f"Data-Agent repo: {len(files)} changed file(s) in {groups}. "
        "Use make test-changed for verification; keep generated OpenAPI files in sync "
        "with make api-types when API schemas change."
    )


def diff_check() -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "diff", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    event = os.environ.get("AGENT_HOOK_EVENT", "")
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    if event in {"SessionStart", "sessionStart"}:
        emit({"additionalContext": context()})
        return 0

    if event in {"PostToolUse", "afterFileEdit", "postToolUse"}:
        ok, details = diff_check()
        if not ok:
            emit(
                {
                    "additionalContext": (
                        "git diff --check found whitespace errors; fix them before finishing. "
                        f"Details: {details}"
                    )
                }
            )
        else:
            # No state file is needed: the next event can derive scope from git.
            pass
        return 0

    if event in {"Stop", "stop", "SessionEnd", "sessionEnd"}:
        if any(path.endswith((".py", ".ts", ".tsx", ".js", ".jsx")) for path in changed_files()):
            emit({"additionalContext": "Before claiming completion, run make test-changed."})
        return 0

    # Unknown events are intentionally no-ops so a runner upgrade cannot block work.
    _ = input_data
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
