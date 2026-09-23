"""The serving gate: read a benchmark report, compare it to budgets, exit non-zero.

Split from `run.py` for the same reason `model/eval` splits its two: running
produces evidence, gating is a judgement about it. A pipeline can archive a
failing report instead of losing it, and a report can be re-judged against
changed budgets without occupying the GPU again.

Three exit codes, because a serving gate has a third answer that a correctness
gate does not:

    0  PASSED        every budget was judged and met
    1  FAILED        a budget was judged and missed
    2  INCONCLUSIVE  a budget could not be judged from this report

Inconclusive is not a softer failure, it is a different statement. This GPU
also serves the deployed stack, so a scenario can be measured while somebody
else's request is in flight; those latencies are partly theirs. Reporting that
as a pass would certify their traffic, and reporting it as a failure would
blame this change for it. The honest answer is that the run cannot decide, and
the fix is to re-run on a quiet server rather than to widen a bound.

What is judged, and what is deliberately not, is argued in thresholds.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.json"

PASSED, FAILED, INCONCLUSIVE = 0, 1, 2


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _budgets(profile: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in profile.items() if not k.startswith("_")}


class Verdict:
    """One budget's outcome, kept as data so the summary and the exit code
    cannot disagree with each other."""

    def __init__(self, label: str, state: str, detail: str, value: Any = None) -> None:
        self.label, self.state, self.detail, self.value = label, state, detail, value


def check(report: dict[str, Any], thresholds: dict[str, Any], at_concurrency: int) -> list[Verdict]:
    verdicts: list[Verdict] = []
    scenarios = report.get("scenarios", [])
    forced = bool(report.get("force_output"))

    # Errors and preemption are judged across EVERY scenario, not only the quiet
    # one: a run that saturated the server at concurrency 8 measured a saturated
    # server, whatever it then reported at concurrency 1.
    limits = thresholds.get("every_scenario", {})
    for scenario in scenarios:
        where = f"{scenario['profile']} @ c{scenario['concurrency']}"
        if scenario.get("errors", 0) > limits.get("errors_max", 0):
            verdicts.append(Verdict(
                f"errors · {where}", "FAIL",
                f"{scenario['errors']} of {scenario['requests']} requests failed",
            ))
        preemptions = (scenario.get("server") or {}).get("preemptions")
        if preemptions is not None and preemptions > limits.get("preemptions_max", 0):
            verdicts.append(Verdict(
                f"preemption · {where}", "FAIL",
                f"{preemptions} request(s) evicted — the server ran out of KV room",
            ))

    judged = {s["profile"]: s for s in scenarios if s["concurrency"] == at_concurrency}
    for name, profile in thresholds.get("profiles", {}).items():
        budgets = _budgets(profile)
        if not budgets:
            continue
        scenario = judged.get(name)
        if scenario is None:
            # Rule borrowed from model/eval's gate: "not measured" is not "fine".
            verdicts.append(Verdict(
                name, "FAIL",
                f"budgeted but not measured at concurrency {at_concurrency}",
            ))
            continue
        if scenario.get("contended"):
            verdicts.append(Verdict(
                name, "INCONCLUSIVE",
                f"{scenario['contended']} other request(s) were in flight when it started",
            ))
            continue

        if "prompt_tokens_max" in budgets:
            value = scenario.get("prompt_tokens_p50")
            bound = budgets["prompt_tokens_max"]
            if value is None:
                verdicts.append(Verdict(f"{name} · prompt_tokens", "INCONCLUSIVE",
                                        "the report records no prompt token count"))
            else:
                state = "FAIL" if value > bound else "ok"
                verdicts.append(Verdict(f"{name} · prompt_tokens", state,
                                        f"max {bound}", value))

        if "decode_tps_min" in budgets:
            bound = budgets["decode_tps_min"]
            value = (scenario.get("decode_tps") or {}).get("p50")
            if not forced:
                verdicts.append(Verdict(
                    f"{name} · decode_tps", "INCONCLUSIVE",
                    "natural output lengths are too short to compare — re-run with "
                    "--force-output",
                ))
            elif value is None:
                verdicts.append(Verdict(f"{name} · decode_tps", "INCONCLUSIVE",
                                        "no decode rate in the report"))
            else:
                state = "FAIL" if value < bound else "ok"
                verdicts.append(Verdict(f"{name} · decode_tps", state, f"min {bound}", value))

        if "prefix_cache_hit_rate_min" in budgets:
            bound = budgets["prefix_cache_hit_rate_min"]
            value = (scenario.get("server") or {}).get("prefix_cache_hit_rate")
            if value is None:
                verdicts.append(Verdict(
                    f"{name} · prefix_cache", "INCONCLUSIVE",
                    "no server metrics in this report (LiteLLM does not expose /metrics; "
                    "judge cache behaviour on a --target direct run)",
                ))
            else:
                state = "FAIL" if value < bound else "ok"
                verdicts.append(Verdict(f"{name} · prefix_cache", state, f"min {bound}", value))

    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate a change on a serving benchmark report.")
    parser.add_argument("--report", default="bench-report.json")
    parser.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    parser.add_argument("--at-concurrency", type=int, default=1,
                        help="which scenario to judge budgets against (default 1 — the quiet, "
                             "reproducible one; see thresholds.json for why load is not judged)")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"GATE FAILED: no report at {report_path}", file=sys.stderr)
        return FAILED

    report = json.loads(report_path.read_text(encoding="utf-8"))
    verdicts = check(report, load_thresholds(Path(args.thresholds)), args.at_concurrency)

    forced = " · forced output" if report.get("force_output") else ""
    print("=" * 78)
    print(f"Serving gate · {report.get('target', '?')}{forced} · "
          f"judging concurrency {args.at_concurrency}")
    print("=" * 78)
    for verdict in verdicts:
        shown = "" if verdict.value is None else f"{verdict.value:>9}"
        mark = {"ok": "ok  ", "FAIL": "FAIL", "INCONCLUSIVE": "?   "}[verdict.state]
        print(f"  {verdict.label:<38} {shown:>10}  {mark}  ({verdict.detail})")
    print("=" * 78)

    failures = [v for v in verdicts if v.state == "FAIL"]
    unknown = [v for v in verdicts if v.state == "INCONCLUSIVE"]
    if failures:
        print("\nGATE FAILED:", file=sys.stderr)
        for verdict in failures:
            print(f"  - {verdict.label}: {verdict.value} against {verdict.detail}", file=sys.stderr)
        return FAILED
    if unknown:
        print("\nGATE INCONCLUSIVE — these budgets could not be judged from this report:",
              file=sys.stderr)
        for verdict in unknown:
            print(f"  - {verdict.label}: {verdict.detail}", file=sys.stderr)
        return INCONCLUSIVE

    print("\nGATE PASSED")
    return PASSED


if __name__ == "__main__":
    raise SystemExit(main())
