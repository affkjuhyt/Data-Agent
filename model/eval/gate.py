"""The release gate: read a report, compare it to thresholds, exit non-zero.

Separate from `run.py` on purpose. Running produces evidence; gating is a
judgement about it. Keeping them apart means a pipeline can archive a failing
report as an artifact instead of losing it when the step dies, and means the
same report can be re-judged against changed thresholds without re-running a
model.

Three rules, and the second is the one that makes this a gate rather than a
dashboard:

  1. A metric outside its bound fails.
  2. A metric the thresholds require, that the report does not contain, fails.
     "Not measured" is not "fine" — the failure mode this whole file exists to
     prevent is a release certified by an eval that silently did not run.
  3. A report whose tiers do not include a tier the caller asked to enforce
     fails, for the same reason.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.json"


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def check(report: dict[str, Any], thresholds: dict[str, Any], tiers: list[str]) -> list[str]:
    """Every reason this report should not ship. Empty means it may."""
    failures: list[str] = []
    measured = report.get("metrics", {})
    ran = report.get("tiers", [])

    for tier in tiers:
        if tier not in ran:
            failures.append(f"tier {tier!r} was not run (report contains: {ran or 'nothing'})")
            continue

        for name, bound in thresholds.get(tier, {}).items():
            if name not in measured:
                failures.append(f"{name}: required by the {tier} tier but not present in the report")
                continue
            value = measured[name]
            if "min" in bound and value < bound["min"]:
                failures.append(f"{name}: {value:.4f} is below the minimum {bound['min']:.4f}")
            if "max" in bound and value > bound["max"]:
                failures.append(f"{name}: {value:.4f} is above the maximum {bound['max']:.4f}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate a release on an evaluation report.")
    parser.add_argument("--report", default="eval-report.json")
    parser.add_argument(
        "--tier", action="append", choices=["offline", "online"],
        help="Tier to enforce; repeatable. Defaults to whichever tiers the report says it ran.",
    )
    parser.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        # A missing report is the loudest version of rule 2.
        print(f"GATE FAILED: no report at {report_path}", file=sys.stderr)
        return 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    thresholds = load_thresholds(Path(args.thresholds))
    tiers = args.tier or report.get("tiers", [])
    if not tiers:
        print("GATE FAILED: the report records no tiers, so there is nothing to enforce", file=sys.stderr)
        return 1

    failures = check(report, thresholds, tiers)

    print("=" * 62)
    print(f"Release gate · enforcing: {', '.join(tiers)}")
    print("=" * 62)
    for tier in tiers:
        for name, bound in thresholds.get(tier, {}).items():
            value = report.get("metrics", {}).get(name)
            limit = f"min {bound['min']}" if "min" in bound else f"max {bound['max']}"
            flag = " (provisional)" if bound.get("provisional") else ""
            if value is None:
                print(f"  {name:<28}   NOT MEASURED   ({limit}){flag}")
            else:
                verdict = "ok  " if not any(name in f for f in failures) else "FAIL"
                print(f"  {name:<28} {value:>8.4f}  {verdict}  ({limit}){flag}")
    print("=" * 62)

    if failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nGATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
