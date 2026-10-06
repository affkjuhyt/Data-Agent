"""Fail on ruff findings a change adds, not on the ones the tree already had.

`make lint` and the CI python-lint job call this. The tree was never held to ruff
(CLAUDE.md §1, "Gates that are currently red"), so a plain `ruff check .` is red on
every branch and proves nothing. This compares the change against its base instead,
which is the rule CLAUDE.md §8 already sets for a change: no *new* findings.

- lint: per file and rule code, the head may not have more findings than the base.
  Counting instead of matching locations keeps an edit that moves lines from
  reporting every old finding below it as new;
- format: a file that was ruff-formatted at the base, or is new, must still be.
  A file the base already left unformatted may stay that way, so the repo-wide
  `ruff format .` stays its own commit.

The base is a directory holding the base commit's `pyproject.toml` and `*.py` files
(`git archive <base> -- pyproject.toml '*.py'`), so both sides use their own config
and no git is needed here. A renamed file counts as new. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

FORMAT_PREFIX = "Would reformat: "


def ruff(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["ruff", *args, "--no-cache", "."], cwd=root, capture_output=True, text=True
    )
    # 0 clean, 1 findings; anything else is ruff failing (bad config, crash).
    if proc.returncode not in (0, 1):
        sys.exit(f"ruff {' '.join(args)} failed in {root}:\n{proc.stderr}")
    return proc


def lint_findings(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = json.loads(
        ruff(root, "check", "--output-format", "json", "--exit-zero").stdout
    )
    for finding in findings:
        finding["path"] = Path(str(finding["filename"])).resolve().relative_to(root).as_posix()
        # ruff reports a syntax error without a rule code.
        finding["code"] = finding["code"] or "syntax-error"
    return findings


def unformatted(root: Path) -> set[str]:
    out = ruff(root, "format", "--check").stdout
    return {
        line.removeprefix(FORMAT_PREFIX).strip()
        for line in out.splitlines()
        if line.startswith(FORMAT_PREFIX)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--head-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    base, head = args.base_dir.resolve(), args.head_dir.resolve()

    base_counts = Counter((str(f["path"]), str(f["code"])) for f in lint_findings(base))
    head_findings = lint_findings(head)
    head_counts = Counter((str(f["path"]), str(f["code"])) for f in head_findings)
    added = {key: n for key, n in head_counts.items() if n > base_counts[key]}

    for (path, code), n in sorted(added.items()):
        print(f"{path}: {code} {base_counts[(path, code)]} -> {n}")
        # Which of them are new cannot be told apart by location, so list them all.
        for f in head_findings:
            if (f["path"], f["code"]) == (path, code):
                loc = f["location"]
                assert isinstance(loc, dict)
                print(f"  {path}:{loc['row']}:{loc['column']} {code} {f['message']}")

    base_files = {p.relative_to(base).as_posix() for p in base.rglob("*.py")}
    newly_unformatted = sorted(unformatted(head) - unformatted(base))
    for path in newly_unformatted:
        was = "is new" if path not in base_files else "was formatted at the base"
        print(f"{path}: not ruff-formatted, and it {was}  (ruff format {path})")

    print(
        f"ruff ratchet: {sum(head_counts.values())} finding(s) in the tree, "
        f"{sum(n - base_counts[k] for k, n in added.items())} added; "
        f"{len(newly_unformatted)} file(s) newly unformatted"
    )
    return 1 if added or newly_unformatted else 0


if __name__ == "__main__":
    sys.exit(main())
