"""What one user question costs end to end, and where the time went.

`run.py` measures single LLM calls. It cannot answer the question that decides
routing and cascade work: a question arriving at the agent fans out into a
planner call, retrieval, a rerank, tool execution, a responder stream and a
judge — how many of those are sequential, and how much of the wall clock is
actually spent inside a model?

Nothing here re-implements the pipeline to find out. The product already
records every step: `AgentTraces` rows carry `tool_name` and `latency_ms` per
`trace_index`, and the message carries its own `latency_ms` and `tokens_used`.
So this asks the real endpoint a real question, times the stream from outside,
then reads back the trace the run itself wrote. A harness that timed its own
reconstruction of the graph would report the reconstruction.

The number to look at first is `unaccounted_ms` — wall clock minus the sum of
the recorded stages. Large means the time is going somewhere no stage records
(queueing, the gateway, serialisation, DB waits); small means the stages are
the whole story and the way to make it faster is to have fewer or cheaper ones.

    export BENCH_AGENT_URL=http://<node>:30240
    export BENCH_AGENT_ID=<uuid> BENCH_TENANT_ID=<uuid>
    export GATEWAY_KEY=<the runtime's gateway key>
    python -m model.bench.e2e --repeat 3 --out e2e-report.json

Credentials come from the environment and are never written to the report. The
runtime trusts either a Kong-verified request or a signed service assertion
(backend/libs/shared/auth.py); this takes the first path, which is what a
browser takes, so the measurement includes the same auth work a user pays for.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from model.bench import profiles as bench_profiles


class E2EError(Exception):
    """A run that cannot produce a comparable number — missing configuration, an
    endpoint that refuses. Fatal: a partial end-to-end report reads as though
    the pipeline were shorter than it is."""


def _settings() -> dict[str, str]:
    missing = [
        name for name in ("BENCH_AGENT_URL", "BENCH_AGENT_ID", "BENCH_TENANT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise E2EError(
            f"set {', '.join(missing)} — this measures a deployed agent, there is no "
            f"offline mode that would mean anything"
        )
    if not (os.environ.get("GATEWAY_KEY") or os.environ.get("KB_GATEWAY_KEY")):
        raise E2EError(
            "set GATEWAY_KEY — the runtime rejects a request the gateway did not verify "
            "(backend/libs/shared/auth.py)"
        )
    return {
        "url": os.environ["BENCH_AGENT_URL"].rstrip("/"),
        "agent_id": os.environ["BENCH_AGENT_ID"],
        "tenant_id": os.environ["BENCH_TENANT_ID"],
        "user_id": os.environ.get("BENCH_USER_ID", "bench"),
        "gateway_key": os.environ.get("GATEWAY_KEY") or os.environ["KB_GATEWAY_KEY"],
    }


def _headers(cfg: dict[str, str], user_ref: str | None = None) -> dict[str, str]:
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": cfg["gateway_key"],
        "X-User-Id": user_ref or cfg["user_id"],
        "X-User-Roles": os.environ.get("BENCH_USER_ROLES", "admin"),
        "X-Tenant-Id": cfg["tenant_id"],
        "Content-Type": "application/json",
    }


async def ask(
    http: httpx.AsyncClient, cfg: dict[str, str], question: str, user_ref: str,
) -> dict[str, Any]:
    """One turn over the SSE endpoint, timed from outside.

    No conversation id is sent. An invented one would be discarded: the runtime
    only continues a thread the caller already owns, and starts a fresh one
    otherwise (conversation_runner.py, "is not owned by the caller"). So the id
    is found afterwards instead, by the unique `user_ref` this turn ran under.
    """
    payload = {"query": question, "agent_id": cfg["agent_id"]}
    started = time.monotonic()
    first_token: float | None = None
    text: list[str] = []
    error: str | None = None

    try:
        async with http.stream(
            "POST", f"{cfg['url']}/api/conversations/run",
            json=payload, headers=_headers(cfg, user_ref),
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                error = f"HTTP {response.status_code}: {body[:200]}"
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.startswith("[DONE]"):
                        break
                    if raw.startswith("[ERROR]"):
                        error = raw[:200]
                        break
                    try:
                        piece = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    # The stream carries answer text as bare strings; the dict
                    # frames are evidence, citations, charts — not tokens.
                    if not isinstance(piece, str) or not piece:
                        continue
                    if first_token is None:
                        first_token = time.monotonic()
                    text.append(piece)
    except (httpx.HTTPError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:200]

    total_ms = (time.monotonic() - started) * 1000
    return {
        "question": question,
        "user_ref": user_ref,
        "ttft_ms": None if first_token is None else round((first_token - started) * 1000, 1),
        "total_ms": round(total_ms, 1),
        "answer_chars": len("".join(text)),
        "error": error,
    }


async def find_conversation(
    http: httpx.AsyncClient, cfg: dict[str, str], user_ref: str,
) -> tuple[str | None, str]:
    """The conversation this turn created, found by its unique `user_ref`.

    `?q=` filters on the conversation id and the user_ref, not on message text,
    which is exactly enough: every turn runs under an id of its own, so the
    match is one row rather than the newest row — and "the newest row" would be
    a race against whatever else is talking to this agent.
    """
    try:
        response = await http.get(
            f"{cfg['url']}/api/conversations",
            params={"q": user_ref, "agent_id": cfg["agent_id"], "limit": 5},
            headers=_headers(cfg), timeout=30.0,
        )
    except (httpx.HTTPError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]
    if response.status_code != 200:
        return None, f"HTTP {response.status_code} listing conversations"
    for item in response.json().get("items", []):
        if item.get("user_ref") == user_ref:
            return str(item["id"]), "ok"
    return None, f"no conversation with user_ref={user_ref}"


async def stages(
    http: httpx.AsyncClient, cfg: dict[str, str], conversation_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """The trace the turn wrote, as the product's own UI reads it.

    Returns why it is empty when it is. A silent [] here is what made the first
    run of this harness report every millisecond as unaccounted: the traces
    existed, the read was asking for the wrong conversation, and nothing said so.
    """
    try:
        response = await http.get(
            f"{cfg['url']}/api/conversations/{conversation_id}/messages",
            headers=_headers(cfg), timeout=30.0,
        )
    except (httpx.HTTPError, OSError) as exc:
        return [], f"{type(exc).__name__}: {exc}"[:200]
    if response.status_code != 200:
        return [], f"HTTP {response.status_code} reading messages"

    messages = response.json().get("messages", [])
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for trace in message.get("traces", []):
            out.append({
                "index": trace.get("trace_index"),
                "stage": trace.get("tool_name"),
                "latency_ms": trace.get("latency_ms"),
                "status": trace.get("status"),
            })
        out.append({
            "index": None, "stage": "__message__",
            "latency_ms": message.get("latency_ms"),
            "tokens_used": message.get("tokens_used"),
        })
    if not out:
        return [], f"no assistant message in {len(messages)} message(s)"
    return out, "ok"


def _summarise(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-stage totals across the turns, plus what no stage accounts for."""
    by_stage: dict[str, list[float]] = defaultdict(list)
    unaccounted: list[float] = []
    for turn in turns:
        recorded = 0.0
        for stage in turn.get("stages", []):
            name, latency = stage.get("stage"), stage.get("latency_ms")
            if name == "__message__" or latency is None:
                continue
            by_stage[name].append(float(latency))
            recorded += float(latency)
        if not turn.get("error"):
            unaccounted.append(turn["total_ms"] - recorded)

    totals = {
        name: {
            "calls_per_turn": round(len(values) / max(len(turns), 1), 2),
            "ms_p50": round(statistics.median(values), 1),
            "ms_total_p50_share": None,
        }
        for name, values in sorted(by_stage.items())
    }
    wall = statistics.median([t["total_ms"] for t in turns]) if turns else 0.0
    for name, values in by_stage.items():
        per_turn = sum(values) / max(len(turns), 1)
        totals[name]["ms_total_p50_share"] = round(per_turn / wall, 3) if wall else None
    return {
        "turns": len(turns),
        "errors": sum(1 for t in turns if t.get("error")),
        "ttft_ms_p50": round(statistics.median(
            [t["ttft_ms"] for t in turns if t["ttft_ms"] is not None]), 1)
            if any(t["ttft_ms"] is not None for t in turns) else None,
        "total_ms_p50": round(wall, 1),
        "unaccounted_ms_p50": round(statistics.median(unaccounted), 1) if unaccounted else None,
        "stages": totals,
    }


async def run(
    questions: list[str], *, repeat: int, timeout_s: float, settle_s: float = 1.0,
) -> dict[str, Any]:
    cfg = _settings()
    run_id = f"bench-{uuid.uuid4().hex[:8]}"
    turns: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout_s) as http:
        for round_index in range(repeat):
            for index, question in enumerate(questions):
                # One user_ref per turn: it is what the conversation is found
                # by afterwards, and it keeps these turns out of anybody's
                # real conversation history.
                user_ref = f"{run_id}-{round_index}-{index}"
                turn = await ask(http, cfg, question, user_ref)
                turn["round"] = round_index
                if turn.get("error"):
                    turn["stages"], turn["trace_fetch"] = [], "turn failed"
                    turns.append(turn)
                    continue
                # The traces are written before the stream closes, but the read
                # is a different connection — give the commit a moment.
                await asyncio.sleep(settle_s)
                conversation_id, why = await find_conversation(http, cfg, user_ref)
                turn["conversation_id"] = conversation_id
                if conversation_id is None:
                    turn["stages"], turn["trace_fetch"] = [], why
                else:
                    turn["stages"], turn["trace_fetch"] = await stages(http, cfg, conversation_id)
                turns.append(turn)
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "agent_url": cfg["url"],
        "run_id": run_id,
        "questions": len(questions),
        "repeat": repeat,
        "summary": _summarise(turns),
        "turns": turns,
    }


def _print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("=" * 82)
    print(f"End-to-end · {report['agent_url']} · {summary['turns']} turns · "
          f"{report['started_at']}")
    print("=" * 82)
    def ms(value: Any) -> str:
        return "not measured" if value is None else f"{value} ms"

    print(f"  time to first token   p50  {ms(summary['ttft_ms_p50'])}")
    print(f"  whole turn            p50  {ms(summary['total_ms_p50'])}")
    print(f"  recorded by no stage  p50  {ms(summary['unaccounted_ms_p50'])}")
    if summary["errors"]:
        print(f"  errors                     {summary['errors']} of {summary['turns']}")
        for turn in report["turns"]:
            if turn.get("error"):
                print(f"      {turn['error']}")
                break
    if not summary["stages"]:
        reasons = sorted({t.get("trace_fetch") or "?" for t in report["turns"]})
        print("\n  No stage traces came back, so the per-stage question is unanswered, "
              "not zero. Why:")
        for reason in reasons:
            print(f"      {reason}")
    else:
        print(f"\n  {'stage':<28} {'calls/turn':>11} {'ms p50':>9} {'share of turn':>14}")
        print("  " + "-" * 66)
        for name, stage in summary["stages"].items():
            share = stage["ms_total_p50_share"]
            print(f"  {name:<28} {stage['calls_per_turn']:>11} {stage['ms_p50']:>9} "
                  f"{('-' if share is None else f'{share * 100:.0f}%'):>14}")
    print("=" * 82)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure one user question end to end, broken down by stage.")
    parser.add_argument("--repeat", type=int, default=3, help="passes over the question set")
    parser.add_argument("--questions", type=int, default=5,
                        help="how many corpus questions to ask (they come from "
                             "model/eval/cases, the same ones the eval grades)")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0,
                        help="wait between a turn and reading back its traces")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    try:
        questions = list(bench_profiles.load()["planner"].user_turns)[: args.questions]
        report = asyncio.run(run(questions, repeat=args.repeat, timeout_s=args.timeout,
                                 settle_s=args.settle_seconds))
    except (E2EError, bench_profiles.ProfileError) as exc:
        print(f"e2e: {exc}", file=sys.stderr)
        return 2

    _print_summary(report)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report → {args.out}")

    summary = report["summary"]
    if summary["errors"] == summary["turns"]:
        # Every turn failed, so the wall clock measured how fast the agent
        # refuses. Exiting 0 here would read as a successful measurement, which
        # is the one thing a benchmark must never do.
        print("\ne2e: every turn failed — nothing was measured", file=sys.stderr)
        return 2
    if summary["errors"]:
        print(f"\ne2e: {summary['errors']} of {summary['turns']} turns failed; the medians "
              f"cover the rest", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
