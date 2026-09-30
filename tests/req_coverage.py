"""Requirement coverage: which requirement has which passing test.

Merges the per-suite JSON that `pytest --req-report-dir DIR` writes (tests/conftest.py)
and vitest's JSON reporter output (`vitest run --reporter=json --outputFile=…`, requirement
IDs in square brackets in the test name, e.g. `[NOP-248][contract] shows 409`) into one
table per requirement ID. Rules and the ID format: docs/v2/15-test-strategy.md §7.

    python tests/req_coverage.py reports/req/*.json
    python tests/req_coverage.py reports/req/*.json --expect docs/v2/data/test-traceability.csv --milestone C1

`--expect FILE` lists every expected requirement that is failing, has no passing test, or
lacks a passing test at one of the levels/kinds it needs, and exits 1 when there is one, so a milestone gate can fail on an
uncovered Must issue. A CSV (the traceability matrix) contributes each row's `issue` with the
tags named in its `test_levels` column, optionally only rows whose `milestone` is one of
`--milestone`; any other file contributes every requirement ID in its text, needing a passing
test at any level. Stdlib only: it reads reports, it never imports a service.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Same ID grammar as tests/conftest.py.
REQ_ID = re.compile(r"\b(?:UC\d{2}|NOP-\d+|SC27-\d{2}|SCVN-\d|[A-Z][A-Z0-9]*-\d{2}[a-z]?)\b")
_BRACKET = re.compile(r"\[([A-Za-z0-9_-]+)\]")
_KINDS = {"contract", "security", "resilience", "performance", "ai_eval"}


def _from_vitest(doc: dict) -> list[dict]:
    rows = []
    for file_result in doc.get("testResults", []):
        for a in file_result.get("assertionResults", []):
            name = a.get("fullName") or a.get("title", "")
            tags = _BRACKET.findall(name)
            rows.append(
                {
                    "nodeid": f"{file_result.get('name', '')}::{name}",
                    "level": "unit",
                    "kinds": sorted(t for t in tags if t in _KINDS),
                    "reqs": sorted({t for t in tags if REQ_ID.fullmatch(t)}),
                    "outcome": {"passed": "passed", "failed": "failed"}.get(
                        a.get("status"), "skipped"
                    ),
                }
            )
    return rows


def load(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        doc = json.loads(p.read_text())
        rows.extend(doc["tests"] if doc.get("source") == "pytest" else _from_vitest(doc))
    return rows


_TAGS = {"unit", "integration", "e2e"} | _KINDS
# Rows that are judged by people, not by a test (docs/v2/15-test-strategy.md §3).
_UNTESTED_LAYERS = {"Feature", "PM"}


def expected_ids(path: Path, milestones: set[str]) -> dict[str, set[str]]:
    """Requirement ID -> the levels/kinds it needs a passing test at (empty: any level)."""
    if path.suffix != ".csv":
        return {i: set() for i in REQ_ID.findall(path.read_text())}
    with path.open(newline="") as f:
        return {
            row["issue"]: set(re.findall(r"[a-z_0-9]+", row.get("test_levels", ""))) & _TAGS
            for row in csv.DictReader(f)
            if (not milestones or row.get("milestone") in milestones)
            and row.get("layer") not in _UNTESTED_LAYERS
            and row.get("test_levels") != "review"
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--expect", type=Path, action="append", default=[])
    ap.add_argument("--milestone", action="append", default=[], help="C1, C2, C3 (repeatable)")
    args = ap.parse_args(argv)

    by_req: dict[str, list[dict]] = defaultdict(list)
    for row in load(args.reports):
        for req in row["reqs"]:
            by_req[req].append(row)

    print("| Requirement | Tests | Passed | Failed | Levels | Kinds |")
    print("| --- | --- | --- | --- | --- | --- |")
    for req in sorted(by_req, key=lambda r: (r[:2], int(re.sub(r"\D", "", r) or 0))):
        tests = by_req[req]
        passed = sum(t["outcome"] == "passed" for t in tests)
        failed = sum(t["outcome"] == "failed" for t in tests)
        levels = ", ".join(sorted({t["level"] or "?" for t in tests}))
        kinds = ", ".join(sorted({k for t in tests for k in t["kinds"]})) or "-"
        print(f"| {req} | {len(tests)} | {passed} | {failed} | {levels} | {kinds} |")

    expected: dict[str, set[str]] = {}
    for f in args.expect:
        for req, needs in expected_ids(f, set(args.milestone)).items():
            expected.setdefault(req, set()).update(needs)

    # One state per requirement, worst first: failing > uncovered > missing <level> > passing.
    gaps = []
    for req in sorted(expected, key=lambda r: int(re.sub(r"\D", "", r) or 0)):
        tests = by_req.get(req, [])
        passing = [t for t in tests if t["outcome"] == "passed"]
        have = {t["level"] for t in passing} | {k for t in passing for k in t["kinds"]}
        if any(t["outcome"] == "failed" for t in tests):
            gaps.append(f"{req}: failing")
        elif not passing:
            gaps.append(f"{req}: uncovered")
        elif expected[req] - have:
            gaps.append(f"{req}: missing {', '.join(sorted(expected[req] - have))}")
    if gaps:
        print(f"\n{len(gaps)} of {len(expected)} expected requirement(s) not passing:")
        print("\n".join(f"- {g}" for g in gaps))
    elif expected:
        print(f"\nAll {len(expected)} expected requirement(s) passing.")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
