"""Run an evaluation tier and write a report.

Two tiers, split by what they need rather than by what they measure:

  * **offline** — policy-violation rate, false-refusal rate, SQL accuracy. Pure
    Python against the real guards and the real metric compiler; no model, no
    warehouse, no stack. This is what can run on every pull request, which is
    the only reason a quality gate exists at all rather than existing on paper.
  * **online** — intent accuracy and hallucination rate. Needs a model behind
    LiteLLM and, for the answering step, a live agent. This is the release tier.

The report is JSON so `gate.py` can decide, and so a run can be attached to a
release as evidence of what was measured rather than a claim that it was.

    python -m model.eval.run --tier offline --out eval-report.json
    python -m model.eval.gate --report eval-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from model.eval import corpus
from model.eval.scorers import grounding, intent, policy, sql


def run_offline() -> dict[str, Any]:
    policy_report = policy.score(corpus.load_cases("policy"))
    sql_report = sql.score(corpus.load_cases("sql"), corpus.load_catalogs())
    return {
        "policy": policy_report,
        "sql": sql_report,
        "metrics": {**policy_report["metrics"], **sql_report["metrics"]},
    }


async def run_online(agent_url: str, agent_id: str, data_space_id: str) -> dict[str, Any]:
    from model.eval import agent_client

    intent_report = await intent.score(corpus.load_cases("intent"), intent.planner_classifier)

    async def answer(case: dict[str, Any]) -> str:
        return await agent_client.ask(
            case,
            agent_url=agent_url,
            agent_id=agent_id,
            data_space_id=data_space_id,
        )

    grounding_report = await grounding.score(
        corpus.load_cases("grounding"),
        answer,
        grounding.ragas_faithfulness(),
    )
    return {
        "intent": intent_report,
        "grounding": grounding_report,
        "metrics": {**intent_report["metrics"], **grounding_report["metrics"]},
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 62)
    print(f"Evaluation report · tiers: {', '.join(report['tiers'])}")
    print("=" * 62)
    for name, value in sorted(report["metrics"].items()):
        bar = "#" * round(value * 20)
        print(f"  {name:<28} {value:.4f}  {bar}")
    for section in ("policy", "sql", "intent", "grounding"):
        failures = [
            r for r in report.get(section, {}).get("results", []) if not r.get("passed", True)
        ]
        if failures:
            print(f"\n  {section} — {len(failures)} failing case(s):")
            for item in failures[:20]:
                detail = item.get("detail") or item.get("reason") or item.get("error") or ""
                print(f"    {item['id']:<12} {item.get('tag', ''):<26} {str(detail)[:70]}")
    print("=" * 62)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Data Agent evaluation corpus.")
    parser.add_argument("--tier", choices=["offline", "online", "all"], default="offline")
    parser.add_argument("--out", default="eval-report.json")
    parser.add_argument(
        "--agent-url", default=os.getenv("EVAL_AGENT_URL", "http://localhost:30100")
    )
    parser.add_argument("--agent-id", default=os.getenv("AGENT_ID", ""))
    parser.add_argument(
        "--tenant-id",
        dest="data_space_id",
        default=os.getenv("EVAL_DATA_SPACE_ID", "a0000000-0000-0000-0000-000000000001"),
    )
    args = parser.parse_args()

    report: dict[str, Any] = {"tiers": [], "metrics": {}}

    if args.tier in ("offline", "all"):
        offline = run_offline()
        report["tiers"].append("offline")
        report["metrics"].update(offline.pop("metrics"))
        report.update(offline)

    if args.tier in ("online", "all"):
        if not args.agent_id:
            print("--agent-id (or AGENT_ID) is required for the online tier", file=sys.stderr)
            return 2
        online = asyncio.run(run_online(args.agent_url, args.agent_id, args.data_space_id))
        report["tiers"].append("online")
        report["metrics"].update(online.pop("metrics"))
        report.update(online)

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\nReport written to {args.out}")
    # Running is not judging: the exit code here says the run completed, and
    # gate.py says whether it is good enough. Keeping them separate is what lets
    # a release pipeline record a failing report instead of losing it to a
    # non-zero exit halfway through.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
