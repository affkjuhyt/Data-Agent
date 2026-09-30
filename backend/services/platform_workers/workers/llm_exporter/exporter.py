#!/usr/bin/env python3
"""
llm_exporter — turns the LLM benchmark into a Prometheus scrape target so LLM
generation performance (TTFT, tok/s, concurrent behaviour) shows up live in the
same Grafana "KB — High CCU" dashboard as the gateway + embed metrics.

A background thread periodically probes the LLM (OpenAI-compatible
/v1/chat/completions, streaming) — one single request + one small concurrent
batch — and records the timings as gauges. Prometheus scrapes GET /metrics.

Env:
  LLM_BASE_URL     base URL of the LLM (default http://10.10.55.101:8001)
  LLM_MODEL        model name (default agent-main)
  PROBE_INTERVAL   seconds between probe cycles (default 30)
  PROBE_CONCURRENCY number of parallel requests in the concurrent probe (default 3)
  LISTEN_PORT      /metrics port (default 8270)
"""
import json
import os
import threading
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

BASE_URL    = os.getenv("LLM_BASE_URL", "http://10.10.55.101:8001").rstrip("/")
MODEL       = os.getenv("LLM_MODEL", "agent-main")
INTERVAL    = float(os.getenv("PROBE_INTERVAL", "30"))
CONCURRENCY = int(os.getenv("PROBE_CONCURRENCY", "3"))
PORT        = int(os.getenv("LISTEN_PORT", "8270"))

SINGLE_PROMPT     = "List 5 capital cities."
CONCURRENT_PROMPT = "Explain what machine learning is in 5 sentences."

# Shared metric state, guarded by _lock. None = not yet measured / probe failed.
_lock = threading.Lock()
_state = {
    "up": 0,
    "single_ttft_ms": None,
    "single_total_ms": None,
    "single_tps": None,
    "single_tokens": None,
    "conc_agg_tps": None,
    "conc_wall_ms": None,
    "conc_avg_ttft_ms": None,
    "probes_total": 0,
    "errors_total": 0,
}

_probe_started = False
_probe_started_lock = threading.Lock()


def stream_request(messages, max_tokens):
    """One streaming completion; returns TTFT (ms), total (ms), token count."""
    data = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    ttft = None
    tokens = 0
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                delta = json.loads(line[6:])["choices"][0]["delta"]
            except Exception:
                continue
            if delta.get("content"):
                tokens += 1
                if ttft is None:
                    ttft = (time.time() - t0) * 1000
    return {
        "ttft_ms": ttft,
        "total_ms": (time.time() - t0) * 1000,
        "tokens": tokens,
    }


def _tps(r):
    if r["ttft_ms"] and r["tokens"]:
        gen_ms = r["total_ms"] - r["ttft_ms"]
        if gen_ms > 0:
            return r["tokens"] / (gen_ms / 1000)
    return 0.0


def probe_once():
    """Run a single + concurrent probe and update the shared state."""
    # --- single request ---
    single = stream_request([{"role": "user", "content": SINGLE_PROMPT}], 80)

    # --- concurrent batch (measures whether the LLM serialises) ---
    results = [None] * CONCURRENCY

    def worker(i):
        try:
            results[i] = stream_request(
                [{"role": "user", "content": CONCURRENT_PROMPT}], 150)
        except Exception:
            results[i] = None

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENCY)]
    t_wall = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_ms = (time.time() - t_wall) * 1000

    ok = [r for r in results if r]
    total_tokens = sum(r["tokens"] for r in ok)
    ttfts = [r["ttft_ms"] for r in ok if r["ttft_ms"]]
    agg_tps = total_tokens / (wall_ms / 1000) if wall_ms > 0 else 0.0
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else None

    with _lock:
        _state["up"] = 1
        _state["single_ttft_ms"] = single["ttft_ms"]
        _state["single_total_ms"] = single["total_ms"]
        _state["single_tps"] = _tps(single)
        _state["single_tokens"] = single["tokens"]
        _state["conc_agg_tps"] = agg_tps
        _state["conc_wall_ms"] = wall_ms
        _state["conc_avg_ttft_ms"] = avg_ttft
        _state["probes_total"] += 1


def probe_loop():
    while True:
        try:
            probe_once()
        except Exception as e:
            with _lock:
                _state["up"] = 0
                _state["errors_total"] += 1
            print(f"[llm_exporter] probe failed: {e}", flush=True)
        time.sleep(INTERVAL)


def start_probe_loop() -> None:
    global _probe_started
    with _probe_started_lock:
        if _probe_started:
            return
        threading.Thread(target=probe_loop, daemon=True).start()
        _probe_started = True


def render_metrics():
    with _lock:
        s = dict(_state)
    ml = f'model="{MODEL}"'
    lines = [
        "# HELP kb_llm_up 1 if the last LLM probe succeeded",
        "# TYPE kb_llm_up gauge",
        f"kb_llm_up {s['up']}",
        "# HELP kb_llm_single_ttft_ms Time-to-first-token, single request (ms)",
        "# TYPE kb_llm_single_ttft_ms gauge",
        "# HELP kb_llm_single_total_ms Total latency, single request (ms)",
        "# TYPE kb_llm_single_total_ms gauge",
        "# HELP kb_llm_tokens_per_sec Generation speed, single stream (tok/s)",
        "# TYPE kb_llm_tokens_per_sec gauge",
        "# HELP kb_llm_concurrent_agg_tps Aggregate tok/s across the concurrent batch",
        "# TYPE kb_llm_concurrent_agg_tps gauge",
        "# HELP kb_llm_concurrent_wall_ms Wall-clock for the concurrent batch (ms)",
        "# TYPE kb_llm_concurrent_wall_ms gauge",
        "# HELP kb_llm_concurrent_avg_ttft_ms Avg TTFT under concurrency (ms)",
        "# TYPE kb_llm_concurrent_avg_ttft_ms gauge",
        "# HELP kb_llm_probes_total Number of completed probe cycles",
        "# TYPE kb_llm_probes_total counter",
        f"kb_llm_probes_total {s['probes_total']}",
        "# HELP kb_llm_errors_total Number of failed probe cycles",
        "# TYPE kb_llm_errors_total counter",
        f"kb_llm_errors_total {s['errors_total']}",
    ]
    # gauges that are only emitted once measured (avoid publishing stale zeros)
    def emit(name, key):
        if s[key] is not None:
            lines.append(f"{name}{{{ml}}} {s[key]:.3f}")
    emit("kb_llm_single_ttft_ms", "single_ttft_ms")
    emit("kb_llm_single_total_ms", "single_total_ms")
    emit("kb_llm_tokens_per_sec", "single_tps")
    emit("kb_llm_concurrent_agg_tps", "conc_agg_tps")
    emit("kb_llm_concurrent_wall_ms", "conc_wall_ms")
    emit("kb_llm_concurrent_avg_ttft_ms", "conc_avg_ttft_ms")
    return ("\n".join(lines) + "\n").encode()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    print(
        f"[llm_exporter] probing {BASE_URL} model={MODEL} "
        f"every {INTERVAL}s (concurrency={CONCURRENCY})",
        flush=True,
    )
    start_probe_loop()
    yield


app = FastAPI(title="llm_exporter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


def main():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
