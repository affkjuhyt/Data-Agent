"""vLLM's own counters, read either side of a scenario.

Client-side timings say a scenario was slow. They cannot say *why*: a prompt
that missed the prefix cache and a request that was preempted out of a full
batch both look like a slow first token from outside. vLLM publishes both on
`/metrics`, so the run reads them before and after and reports the delta — the
prefix-cache hit rate *for this scenario*, not the server's lifetime average,
which is dominated by whatever ran before.

Absent metrics are not an error. The endpoint is optional (LiteLLM does not
expose it, and vLLM can be started without it); the report then simply has no
server section, which is honest, whereas a zero would read as "no cache hits".
"""
from __future__ import annotations

from typing import Any

import httpx

_COUNTERS = (
    "vllm:prefix_cache_queries_total",
    "vllm:prefix_cache_hits_total",
    "vllm:num_preemptions_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
)
_GAUGES = (
    "vllm:kv_cache_usage_perc",
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
)


def metrics_url(base_url: str) -> str:
    """`http://host:8001/v1` → `http://host:8001/metrics`."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return f"{root}/metrics"


def parse(text: str) -> dict[str, float]:
    """Sum each metric family across its label sets.

    Summing is right for these: one vLLM process serves one model, so the label
    sets are `engine=0` and nothing else. If that ever stops being true, the sum
    is still the total work the server did, which is what the deltas are for.
    """
    wanted = set(_COUNTERS) | set(_GAUGES)
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition("{")
        if rest:
            _, _, value = rest.partition("} ")
        else:
            name, _, value = line.partition(" ")
        if name not in wanted:
            continue
        try:
            out[name] = out.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return out


async def sample(client: httpx.AsyncClient, base_url: str) -> dict[str, float] | None:
    try:
        response = await client.get(metrics_url(base_url), timeout=5.0)
    except (httpx.HTTPError, OSError):
        return None
    if response.status_code != 200:
        return None
    return parse(response.text)


def delta(before: dict[str, float] | None, after: dict[str, float] | None) -> dict[str, Any] | None:
    """What the server did between the two samples.

    `prefix_cache_hit_rate` is over the tokens queried during the scenario, so a
    run against a warm server and a run against a cold one are comparable — the
    lifetime ratio on the endpoint is not.
    """
    if not before or not after:
        return None
    moved = {name: after.get(name, 0.0) - before.get(name, 0.0) for name in _COUNTERS}
    queries = moved["vllm:prefix_cache_queries_total"]
    out: dict[str, Any] = {
        "prefix_cache_queried_tokens": round(queries),
        "prefix_cache_hit_tokens": round(moved["vllm:prefix_cache_hits_total"]),
        "prefix_cache_hit_rate": (
            round(moved["vllm:prefix_cache_hits_total"] / queries, 4) if queries > 0 else None
        ),
        "preemptions": round(moved["vllm:num_preemptions_total"]),
        "prefill_tokens": round(moved["vllm:prompt_tokens_total"]),
        "decoded_tokens": round(moved["vllm:generation_tokens_total"]),
    }
    for gauge in _GAUGES:
        if gauge in after:
            out[gauge.removeprefix("vllm:")] = round(after[gauge], 4)
    return out
