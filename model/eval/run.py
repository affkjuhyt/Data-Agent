"""Run an evaluation tier and write a report.

Two tiers, split by what they need rather than by what they measure:

  * **offline** — policy-violation rate, false-refusal rate, SQL accuracy. Pure
    Python against the real guards and the real metric compiler; no model, no
    warehouse, no stack. This is what can run on every pull request, which is
    the only reason a quality gate exists at all rather than existing on paper.
  * **online** — intent accuracy and hallucination rate. Needs a model behind
    LiteLLM and, for the answering step, a live agent. This is the release tier.
  * **golden** — the golden cases of a dataset (cases/golden/<dataset>/, EV-05) asked through
    the real POST /v1/questions as a person (EVAL_ACCESS_TOKEN, or EVAL_USER/EVAL_PASS), each
    scored by its rubric and expected action (scorers/golden.py, EV-09). Writes what asking
    writes: a conversation, question and answer per case.

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
from model.eval.scorers import causal, chart, grounding, intent, policy, sql


def run_offline() -> dict[str, Any]:
    policy_report = policy.score(corpus.load_cases("policy"))
    sql_report = sql.score(corpus.load_cases("sql"), corpus.load_catalogs())
    why_report = causal.score(corpus.load_cases("why"))
    chart_report = chart.score(corpus.load_cases("charts"))
    return {
        "policy": policy_report,
        "sql": sql_report,
        "why": why_report,
        "charts": chart_report,
        "metrics": {
            **policy_report["metrics"],
            **sql_report["metrics"],
            **why_report["metrics"],
            **chart_report["metrics"],
        },
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


# A golden dataset's Data Space (DATASET_AND_EVALUATION §2.7, §3.5).
GOLDEN_SPACES = {"tlc": "DS-TLC", "edgar": "DS-SEC", "aw": "DS-AW"}


async def run_golden(
    dataset: str,
    *,
    api_url: str,
    keycloak_url: str,
    data_space: str,
    only: list[str] | None = None,
    limit: int | None = None,
    judge: bool = True,
) -> dict[str, Any]:
    from model.eval import agent_client
    from model.eval.scorers import golden

    cases = corpus.load_golden(dataset)
    picked = [
        c
        for c in cases
        if c.get("verification_status") in golden.SCORED_STATUSES and (not only or c["id"] in only)
    ][: limit or None]
    login = agent_client.Login(keycloak_url)
    scored = []
    # One case at a time: the cluster's model serves a person's turns too, and a case's
    # seconds are only comparable between runs when nothing else of the run competes with it.
    for case in picked:
        try:
            asked = await agent_client.ask_golden(
                case, api_url=api_url, data_space=data_space, login=login
            )
        except Exception as exc:  # noqa: BLE001 - a case that errs is scored, not lost
            asked = golden.Asked(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
        result = await golden.score_case(case, asked, golden_judge if judge else None)
        scored.append(result)
        mark = "ok  " if result.passed else "FAIL"
        print(
            f"{mark} {result.id:<14} {result.expected_action:<22} → {result.action:<22}"
            f" {result.trust_code or '-':<12} {result.detail[:80]}",
            flush=True,
        )
    out = golden.report(picked, scored)
    out["dataset"], out["data_space"] = dataset, data_space
    return out


async def golden_judge(
    prompt_id: str, question: str, answer: str, anchors: list[Any]
) -> int | None:
    """The `judge` alias (deploy/docker/litellm/config.yaml), another model than the one answering,
    over LiteLLM's OpenAI-compatible API (the worker image the tier runs in has httpx, not the
    litellm package). None when it cannot be reached or answers off the scale, so the case is
    reported unjudged rather than judged wrong."""
    import httpx

    from model.eval.scorers.golden import JUDGE_PROMPTS

    prompt = JUDGE_PROMPTS[prompt_id].format(anchors=anchors, question=question, answer=answer)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{os.getenv('LITELLM_BASE_URL', 'http://localhost:30500').rstrip('/')}"
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('LITELLM_API_KEY', '')}"},
                json={
                    "model": os.getenv("EVAL_JUDGE_MODEL", "judge"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 300,
                },
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
        score = int(json.loads(text[text.index("{") : text.rindex("}") + 1])["score"])
    except Exception:  # noqa: BLE001 - no judge is "unjudged", never a grade
        return None
    return score if score in (0, 1, 2) else None


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 62)
    print(f"Evaluation report · tiers: {', '.join(report['tiers'])}")
    print("=" * 62)
    for name, value in sorted(report["metrics"].items()):
        bar = "#" * round(value * 20)
        print(f"  {name:<28} {value:.4f}  {bar}")
    for section in ("policy", "sql", "charts", "intent", "grounding", "answers"):
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
    parser.add_argument("--tier", choices=["offline", "online", "golden", "all"], default="offline")
    parser.add_argument("--out", default="eval-report.json")
    parser.add_argument(
        "--agent-url", default=os.getenv("EVAL_AGENT_URL", "http://localhost:30100")
    )
    parser.add_argument("--agent-id", default=os.getenv("AGENT_ID", ""))
    parser.add_argument(
        "--answers",
        default=os.getenv("EVAL_AGENT_ANSWERS", ""),
        help="the JSON of make agent-eval AGENT_EVAL_ARGS=--json: its answers' causal wording (AL-09)",
    )
    parser.add_argument(
        "--tenant-id",
        dest="data_space_id",
        # No default: the demo organisation is not every install's (GEN-06).
        default=os.getenv("EVAL_DATA_SPACE_ID", ""),
    )
    parser.add_argument("--golden", default="tlc", help="the golden dataset (cases/golden/<name>)")
    parser.add_argument(
        "--data-space", default="", help="X-Data-Space of the golden tier (default: the dataset's)"
    )
    parser.add_argument(
        "--keycloak-url", default=os.getenv("EVAL_KEYCLOAK_URL", "http://localhost:30110")
    )
    parser.add_argument("--case", action="append", default=[], help="only this golden case id")
    parser.add_argument("--limit", type=int, default=0, help="the first N golden cases only")
    parser.add_argument("--no-judge", action="store_true", help="leave judge(...) cases unjudged")
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
        if not args.data_space_id:
            print(
                "--tenant-id (or EVAL_DATA_SPACE_ID) is required for the online tier",
                file=sys.stderr,
            )
            return 2
        if not args.answers:
            print(
                "--answers (or EVAL_AGENT_ANSWERS) is required for the online tier: the JSON of"
                " make agent-eval, whose answers the causal scorer reads",
                file=sys.stderr,
            )
            return 2
        online = asyncio.run(run_online(args.agent_url, args.agent_id, args.data_space_id))
        answers = causal.score_answers(json.loads(Path(args.answers).read_text(encoding="utf-8")))
        online["answers"] = answers
        online["metrics"].update(answers["metrics"])
        report["tiers"].append("online")
        report["metrics"].update(online.pop("metrics"))
        report.update(online)

    if args.tier == "golden":
        if not (
            os.getenv("EVAL_ACCESS_TOKEN") or (os.getenv("EVAL_USER") and os.getenv("EVAL_PASS"))
        ):
            print(
                "the golden tier asks as a person: set EVAL_ACCESS_TOKEN, or EVAL_USER and EVAL_PASS",
                file=sys.stderr,
            )
            return 2
        gold = asyncio.run(
            run_golden(
                args.golden,
                api_url=args.agent_url,
                keycloak_url=args.keycloak_url,
                data_space=args.data_space or GOLDEN_SPACES.get(args.golden, ""),
                only=args.case,
                limit=args.limit,
                judge=not args.no_judge,
            )
        )
        report["tiers"].append("golden")
        report["metrics"].update(gold.pop("metrics"))
        report["golden"] = gold

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
