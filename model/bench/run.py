"""Measure what each LLM role costs to serve, and write a report.

    python -m model.bench.run --out bench-report.json
    python -m model.bench.run --target litellm --profile responder --concurrency 1,4,8

Three things are measured per role, because three different optimisations fix
three different numbers and the report exists to say which one to spend on:

  * **ttft_ms** — prefill. Moved by prompt size (context selection, compression,
    prefix caching), not by quantisation. A 2.5k-token system prompt on every
    request shows up here and nowhere else.
  * **decode_tps** — generation speed per request. Moved by weights and kernels
    (AWQ/FP8, speculative decoding), not by prompt size.
  * **throughput_rps at concurrency N** — what the GPU does with a queue.
    Moved by batching and KV-cache room. If it is flat from 1 to 8, the server
    is not batching and no amount of per-request tuning will help the fleet.

Both tiers share one A100, so scenarios run one at a time and the report says
so: a concurrency sweep on `agent-main` is also pressure on `agent-fast`, and
two sweeps in parallel would measure the interference rather than either tier.

There is no `gate.py` next to this the way there is in `model/eval`. Thresholds
here would be invented numbers — this repo has no recorded serving baseline yet.
Record one, then set bounds from it; see README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from model.bench import client as bench_client
from model.bench import profiles as bench_profiles
from model.bench import server_metrics

# What each tier is served as on the GPU host. The alias a service asks LiteLLM
# for is in the Profile; this is the name vLLM itself answers to, needed only by
# --target direct. Kept beside the env var it pairs with rather than in the
# profiles, because it is a fact about the deployment, not about the role.
_TIERS = {
    "main": ("LLM_MAIN_BASE_URL", "agent-main"),
    "fast": ("LLM_FAST_BASE_URL", "agent-fast"),
}


class BenchError(Exception):
    """A run that cannot produce a comparable number — an unreachable endpoint,
    an unknown profile. Fatal: a partial serving report invites the reader to
    compare a measured tier against a missing one."""


def _percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile. No interpolation: with 8-32 samples an
    interpolated p95 is a number that lies between two measurements and was
    never observed, and these reports are read as "how slow can it get"."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-q * len(ordered) // 1))))
    return round(ordered[rank - 1], 1)


def _stats(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 1) if values else None,
    }


def _endpoint(profile: bench_profiles.Profile, target: str) -> tuple[str, str, str]:
    """(base_url, model, api_key) for this profile under this target.

    `direct` bypasses LiteLLM to measure the server alone; `litellm` measures
    what the services actually experience. Running both and subtracting is the
    only way to attribute latency to the proxy, which is why the target is a
    flag rather than a constant.
    """
    if target == "litellm":
        base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
        if not base:
            raise BenchError("LITELLM_BASE_URL is unset; needed for --target litellm")
        key = os.environ.get("LITELLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY", "sk-dev")
        return f"{base}/v1" if not base.endswith("/v1") else base, profile.alias, key
    var, model = _TIERS[profile.tier]
    base = os.environ.get(var, "").rstrip("/")
    if not base:
        raise BenchError(f"{var} is unset; needed to benchmark the {profile.tier} tier")
    return base, model, os.environ.get("REMOTE_LLM_KEY", "not-needed")


async def _scenario(
    http: httpx.AsyncClient,
    profile: bench_profiles.Profile,
    *,
    base_url: str,
    model: str,
    api_key: str,
    concurrency: int,
    repeat: int,
    variant_offset: int,
    force_output: bool,
) -> dict[str, Any]:
    """`repeat` requests with at most `concurrency` in flight, timed together.

    Wall time is taken around the whole batch rather than summed per request:
    with a batching server the per-request times overlap, and adding them up
    would report a throughput the GPU never delivered.
    """
    gate = asyncio.Semaphore(concurrency)
    # vLLM suppresses EOS until min_tokens, so every request generates exactly
    # max_tokens. Decode speed over the 12 tokens a judge naturally emits is
    # mostly noise; over 128 forced ones it is a number that can be compared
    # across runs. It costs realism in content, never in cost-per-token, which
    # is why it is a flag and the report records which way it ran.
    extra_body = {"min_tokens": profile.max_tokens} if force_output else None

    async def one(variant: int) -> bench_client.Timing:
        async with gate:
            return await bench_client.complete(
                http,
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=profile.messages(variant),
                max_tokens=profile.max_tokens,
                temperature=profile.temperature,
                extra_body=extra_body,
            )

    before = await server_metrics.sample(http, base_url)
    # Somebody else's request in flight when the scenario starts is somebody
    # else's latency in the result. This GPU also serves the deployed stack, so
    # that is a normal thing to happen and an abnormal thing to leave unsaid.
    busy = max(
        (before or {}).get("vllm:num_requests_running", 0.0),
        (before or {}).get("vllm:num_requests_waiting", 0.0),
    )
    started = time.monotonic()
    timings = await asyncio.gather(*(one(variant_offset + i) for i in range(repeat)))
    wall_s = time.monotonic() - started
    after = await server_metrics.sample(http, base_url)

    ok = [t for t in timings if t.ok]
    errors = [t.error for t in timings if not t.ok]
    ttft = [t.ttft_ms for t in ok if t.ttft_ms is not None]
    decode = [t.decode_tps for t in ok if t.decode_tps is not None]
    completion_tokens = sum(t.completion_tokens for t in ok)

    scenario: dict[str, Any] = {
        "profile": profile.name,
        "alias": profile.alias,
        "tier": profile.tier,
        "model": model,
        "concurrency": concurrency,
        "requests": repeat,
        "unique_variants": len(profile.user_turns),
        "force_output": force_output,
        "ok": len(ok),
        "errors": len(errors),
        "prompt_tokens_p50": _percentile([float(t.prompt_tokens) for t in ok], 0.50),
        "completion_tokens_p50": _percentile([float(t.completion_tokens) for t in ok], 0.50),
        "ttft_ms": _stats(ttft),
        "total_ms": _stats([t.total_ms for t in ok]),
        "decode_tps": _stats(decode),
        "throughput_rps": round(len(ok) / wall_s, 3) if wall_s > 0 else None,
        "output_tps": round(completion_tokens / wall_s, 1) if wall_s > 0 else None,
        "wall_s": round(wall_s, 2),
    }
    server = server_metrics.delta(before, after)
    if server is not None:
        scenario["server"] = server
    if busy:
        scenario["contended"] = round(busy)
    if errors:
        # First few only: a saturated server fails the same way N times and the
        # report should stay readable.
        scenario["error_samples"] = errors[:3]
    return scenario


async def _warmup(
    http: httpx.AsyncClient, profile: bench_profiles.Profile,
    *, base_url: str, model: str, api_key: str, count: int,
) -> None:
    """Unrecorded requests before the first measurement.

    The first request to an idle vLLM pays for CUDA graph capture and an empty
    prefix cache. That cost is real but it is a cold-start cost, and folding it
    into a p95 that is read as steady-state latency misattributes it.
    """
    for i in range(count):
        await bench_client.complete(
            http, base_url=base_url, api_key=api_key, model=model,
            messages=profile.messages(i), max_tokens=min(profile.max_tokens, 32),
            temperature=profile.temperature,
        )


async def run(
    names: list[str], *, target: str, concurrencies: list[int], repeat: int,
    warmup: int, timeout_s: float, settle_s: float, force_output: bool,
    variant_offset: int,
) -> dict[str, Any]:
    available = bench_profiles.load()
    unknown = [n for n in names if n not in available]
    if unknown:
        raise BenchError(f"unknown profile(s): {', '.join(unknown)}; have {', '.join(available)}")

    scenarios: list[dict[str, Any]] = []
    endpoints: dict[str, str] = {}
    warnings: list[str] = []
    limits = httpx.Limits(max_connections=max(concurrencies) + 4)
    async with httpx.AsyncClient(timeout=timeout_s, limits=limits) as http:
        planned = variant_offset + repeat * len(concurrencies)
        for name in names:
            profile = available[name]
            if planned > len(profile.user_turns):
                # Replayed prompts are served from the prefix cache, so the run
                # would report a hit rate the benchmark manufactured.
                warnings.append(
                    f"{name}: {planned} requests but only {len(profile.user_turns)} distinct "
                    f"prompts — prefix-cache numbers past request "
                    f"{len(profile.user_turns)} are inflated"
                )
            base_url, model, api_key = _endpoint(profile, target)
            endpoints[name] = f"{base_url} ({model})"
            await _warmup(http, profile, base_url=base_url, model=model,
                          api_key=api_key, count=warmup)
            for index, concurrency in enumerate(concurrencies):
                scenarios.append(await _scenario(
                    http, profile, base_url=base_url, model=model, api_key=api_key,
                    concurrency=concurrency, repeat=repeat, force_output=force_output,
                    # Offsetting per level keeps a later level from replaying
                    # the user turns an earlier one just cached; the run-level
                    # offset does the same across separate runs.
                    variant_offset=variant_offset + index * repeat,
                ))
                # Let the batch drain, so the next scenario's first request is
                # not queued behind the previous one's tail.
                await asyncio.sleep(settle_s)

    return {
        "started_at": datetime.now(UTC).isoformat(),
        "target": target,
        "force_output": force_output,
        "variant_offset": variant_offset,
        "endpoints": endpoints,
        "note": "both tiers share one GPU; scenarios ran sequentially",
        "warnings": warnings,
        "scenarios": scenarios,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 96)
    forced = " · forced output length" if report.get("force_output") else ""
    offset = report.get("variant_offset") or 0
    shifted = f" · variant offset {offset}" if offset else ""
    print(f"Serving benchmark · target: {report['target']}{forced}{shifted} · "
          f"{report['started_at']}")
    print("=" * 96)
    header = (f"  {'profile':<22} {'tier':<5} {'conc':>4} {'prompt':>7} {'out':>5} "
              f"{'ttft p50':>9} {'ttft p95':>9} {'tok/s':>7} {'rps':>6} {'hit%':>6} {'err':>4}")
    print(header)
    print("  " + "-" * 92)
    for s in report["scenarios"]:
        hit = (s.get("server") or {}).get("prefix_cache_hit_rate")
        print(f"  {s['profile']:<22} {s['tier']:<5} {s['concurrency']:>4} "
              f"{_num(s['prompt_tokens_p50']):>7} {_num(s['completion_tokens_p50']):>5} "
              f"{_num(s['ttft_ms']['p50']):>9} {_num(s['ttft_ms']['p95']):>9} "
              f"{_num(s['decode_tps']['p50']):>7} {_num(s['throughput_rps']):>6} "
              f"{('-' if hit is None else f'{hit * 100:.0f}'):>6} {s['errors']:>4}")
    for warning in report.get("warnings", []):
        print(f"\n  ! {warning}")
    contended = [s for s in report["scenarios"] if s.get("contended")]
    if contended:
        print("\n  ! other traffic was already on the server when these started — "
              "their latencies include somebody else's work:")
        for s in contended:
            print(f"      {s['profile']} @ concurrency {s['concurrency']}: "
                  f"{s['contended']} request(s) in flight")
    preempted = [s for s in report["scenarios"] if (s.get("server") or {}).get("preemptions")]
    if preempted:
        print("\n  preemption (the server ran out of KV room and evicted running requests):")
        for s in preempted:
            print(f"    {s['profile']} @ concurrency {s['concurrency']}: "
                  f"{s['server']['preemptions']} preemption(s)")
    failed = [s for s in report["scenarios"] if s["errors"]]
    if failed:
        print("\n  errors:")
        for s in failed:
            for sample in s.get("error_samples", []):
                print(f"    {s['profile']} @ {s['concurrency']}: {sample}")
    print("=" * 96)


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the serving tiers behind the agent.")
    parser.add_argument("--target", choices=("direct", "litellm"), default="direct",
                        help="direct: the vLLM tier itself. litellm: through the proxy, "
                             "which is what the services see.")
    parser.add_argument("--profile", action="append", default=[],
                        help="repeatable; default is every profile")
    parser.add_argument("--concurrency", default="1,4",
                        help="comma-separated in-flight levels, e.g. 1,4,8")
    parser.add_argument("--repeat", type=int, default=8, help="requests per level")
    parser.add_argument("--warmup", type=int, default=2, help="unrecorded requests per profile")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--variant-offset", type=int, default=0,
                        help="skip this many prompts before starting. vLLM's prefix cache "
                             "outlives a run, so a second run at offset 0 replays the first "
                             "run's prompts and reports a hit rate it did not earn. Advance "
                             "the offset to measure a cold cache.")
    parser.add_argument("--force-output", action="store_true",
                        help="generate exactly max_tokens every request (vLLM min_tokens). "
                             "Use it to compare decode speed; leave it off to measure the "
                             "workload as the product actually runs it.")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    args = parser.parse_args()

    names = args.profile or list(bench_profiles.load())
    concurrencies = [int(part) for part in args.concurrency.split(",") if part.strip()]
    try:
        report = asyncio.run(run(
            names, target=args.target, concurrencies=concurrencies, repeat=args.repeat,
            warmup=args.warmup, timeout_s=args.timeout, settle_s=args.settle_seconds,
            force_output=args.force_output, variant_offset=args.variant_offset,
        ))
    except (BenchError, bench_profiles.ProfileError) as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    _print_summary(report)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
