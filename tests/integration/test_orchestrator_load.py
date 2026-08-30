"""Load test for POST /api/orchestrate/stream.

Sweeps concurrency levels against a running workflow_runtime (which serves
/api/orchestrate) and reports
latency / error-rate per level, so we can find where the system stops
scaling (LLM backend, connection pool, or the process itself).

As a pytest e2e test (skipped unless LIVE_STACK=1 + workflow_runtime is up):
    LIVE_STACK=1 pytest tests/integration/test_orchestrator_load.py -m e2e -s

As a standalone CLI for ad-hoc sweeps:
    python3 tests/integration/test_orchestrator_load.py --levels 1,5,10,20,40
    python3 tests/integration/test_orchestrator_load.py --host 127.0.0.1 --port 8001 --csv results.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
import uuid

import httpx
import pytest

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
DEFAULT_QUERY = "Explain what machine learning is in 5 sentences."
DEFAULT_LEVELS = [1, 5, 10, 20]


async def run_one(client: httpx.AsyncClient, base_url: str, query: str, timeout: float) -> dict:
    t0 = time.time()
    result = {
        "ok": False,
        "error": None,
        "t_session_ms": None,
        "t_first_token_ms": None,
        "t_done_ms": None,
        "session_id": None,
    }
    # LiteLLM proxy caches completions by (model, messages) in Redis
    # (deploy/docker/litellm/config.yaml: cache: true). Appending a unique nonce to
    # every request's query keeps identical-looking load-test traffic from
    # collapsing into cache hits, which would report fake near-instant
    # latencies instead of real pipeline latency under load.
    nonce = uuid.uuid4().hex[:8]
    payload = {"query": f"{query} [{nonce}]", "user_id": f"loadtest-{nonce}"}
    try:
        async with client.stream(
            "POST", f"{base_url}/api/orchestrate/stream", json=payload, timeout=timeout
        ) as resp:
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                return result
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[len("data: "):]
                now_ms = (time.time() - t0) * 1000
                if raw == "[DONE]":
                    result["t_done_ms"] = now_ms
                    result["ok"] = result["error"] is None
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict) and "__session_id" in obj:
                    result["t_session_ms"] = now_ms
                    result["session_id"] = obj["__session_id"]
                elif isinstance(obj, str) and result["t_first_token_ms"] is None:
                    result["t_first_token_ms"] = now_ms
                # other dict events (__pending_review, __clarification,
                # __suggestions, __conv_ids) are control events, not errors.
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except httpx.ConnectError:
        result["error"] = "connect_error"
    except Exception as e:  # noqa: BLE001 - load test must keep going
        result["error"] = f"{type(e).__name__}: {e}"
    return result


async def run_level(base_url: str, query: str, concurrency: int, timeout: float) -> list:
    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [run_one(client, base_url, query, timeout) for _ in range(concurrency)]
        return await asyncio.gather(*tasks)


def pctl(values: list, p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[k]


def summarize(level: int, results: list) -> dict:
    ok = [r for r in results if r["ok"]]
    errors = [r for r in results if not r["ok"]]
    done_times = [r["t_done_ms"] for r in ok if r["t_done_ms"] is not None]
    ttft = [r["t_first_token_ms"] for r in ok if r["t_first_token_ms"] is not None]
    return {
        "level": level,
        "success": len(ok),
        "errors": len(errors),
        "error_types": sorted({r["error"] for r in errors if r["error"]}),
        "avg_ms": statistics.mean(done_times) if done_times else float("nan"),
        "p50_ms": pctl(done_times, 0.50),
        "p95_ms": pctl(done_times, 0.95),
        "p99_ms": pctl(done_times, 0.99),
        "avg_ttft_ms": statistics.mean(ttft) if ttft else float("nan"),
    }


async def sweep(base_url: str, query: str, levels: list, timeout: float,
                 stop_on_error_rate: float = 0.5) -> list:
    print(f"\nTarget : {base_url}/api/orchestrate/stream")
    print(f"Query  : {query!r}")
    print(f"Levels : {levels}")

    header = f"{'Level':>5} {'OK':>4} {'Err':>4} {'AvgTTFT':>9} {'Avg':>9} {'p50':>9} {'p95':>9} {'p99':>9}"
    print(header)
    print("-" * len(header))

    rows = []
    for level in levels:
        results = await run_level(base_url, query, level, timeout)
        s = summarize(level, results)
        print(
            f"{s['level']:>5} {s['success']:>4} {s['errors']:>4} "
            f"{s['avg_ttft_ms']:>8.0f}m {s['avg_ms']:>8.0f}m "
            f"{s['p50_ms']:>8.0f}m {s['p95_ms']:>8.0f}m {s['p99_ms']:>8.0f}m"
        )
        if s["errors"]:
            print(f"      errors: {s['error_types']}")
        rows.append(s)
        if stop_on_error_rate and s["errors"] / level >= stop_on_error_rate:
            print(f"\nStopping sweep: error rate {s['errors']}/{level} "
                  f"reached threshold {stop_on_error_rate}.")
            break
    return rows


# ─── pytest e2e entrypoint ──────────────────────────────────────────────────
@pytest.mark.e2e
def test_orchestrator_handles_baseline_concurrency():
    """Sanity check: the orchestrator (inside workflow_runtime) survives a small concurrency burst.

    Skipped unless LIVE_STACK=1 and workflow_runtime is reachable — this is
    a smoke assertion, not the full sweep. Run the file directly (see module
    docstring) for the full concurrency-vs-latency report used to size
    scheduling/priority work.
    """
    if os.environ.get("LIVE_STACK") != "1":
        pytest.skip(
            "Live stack not requested — set LIVE_STACK=1 and start the "
            "workflow_runtime (backend/services/workflow_runtime, serves /api/orchestrate) before running."
        )
    base_url = os.environ.get("WORKFLOW_RUNTIME_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    rows = asyncio.run(sweep(base_url, DEFAULT_QUERY, [1, 5], timeout=120.0))
    for row in rows:
        assert row["errors"] == 0, f"level={row['level']} had errors: {row['error_types']}"


# ─── standalone CLI ─────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", default=DEFAULT_PORT, type=int)
    p.add_argument("--query", default=DEFAULT_QUERY)
    p.add_argument("--levels", default="1,5,10,20",
                    help="Comma-separated concurrency levels to sweep, e.g. 1,5,10,20,40")
    p.add_argument("--timeout", default=180.0, type=float,
                    help="Per-request timeout in seconds")
    p.add_argument("--stop-on-error-rate", default=0.5, type=float,
                    help="Stop the sweep early once error rate at a level reaches this fraction (0 disables)")
    p.add_argument("--csv", default=None, help="Optional path to write per-level results as CSV")
    args = p.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    levels = [int(x) for x in args.levels.split(",") if x.strip()]

    rows = asyncio.run(sweep(base_url, args.query, levels, args.timeout, args.stop_on_error_rate))

    if args.csv and rows:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for row in rows:
                w.writerow(row)
        print(f"\nWrote {args.csv}")

    print("\nDone.")


if __name__ == "__main__":
    main()
