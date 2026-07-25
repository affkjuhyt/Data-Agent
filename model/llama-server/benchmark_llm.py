#!/usr/bin/env python3
"""
LLM Benchmark Script — Qwen3 on llama-server
Usage:
  python3 model/llama-server/benchmark_llm.py                  # full suite
  python3 model/llama-server/benchmark_llm.py --single         # single request only
  python3 model/llama-server/benchmark_llm.py --concurrent     # concurrent only
  python3 model/llama-server/benchmark_llm.py --thinking       # thinking mode only
  python3 model/llama-server/benchmark_llm.py --memory         # memory stats only
  python3 model/llama-server/benchmark_llm.py --host 127.0.0.1 --port 8088
"""
import argparse, json, os, sys, threading, time, urllib.request

DEFAULT_HOST  = "127.0.0.1"
DEFAULT_PORT  = 8088
DEFAULT_MODEL = "qwen3-8b-instruct-q4_k_m.gguf"

SINGLE_PROMPTS = [
    ("short",   "Say hello in one sentence. /no_think",           50),
    ("medium",  "List 5 capital cities. /no_think",               80),
    ("long",    "Explain TCP handshake in 3 steps. /no_think",   150),
]

CONCURRENT_PROMPTS = [
    "Explain what machine learning is in 5 sentences. /no_think",
    "Describe how a web server handles HTTP requests in 5 sentences. /no_think",
    "Differences between SQL and NoSQL databases in 5 points. /no_think",
    "Explain containerization and Docker in 5 sentences. /no_think",
]

THINKING_PROMPTS = [
    ("math",    "What is 17 * 23? Think step by step.",  1024),
    ("complex", "Explain TCP handshake in 3 steps.",     1024),
]


def get_server_pid():
    try:
        return int(os.popen("pgrep -f 'llama-server' | head -1").read().strip())
    except:
        return None


def get_rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except:
        pass
    return 0


def health_check(base_url):
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=3) as r:
            return b'"ok"' in r.read()
    except:
        return False


def stream_request(base_url, model, messages, max_tokens):
    data = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    tt_think = tt_content = None
    think_tok = content_tok = 0
    finish_reason = None

    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                chunk  = json.loads(line[6:])
                choice = chunk["choices"][0]
                delta  = choice["delta"]
                now    = (time.time() - t0) * 1000
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                if delta.get("reasoning_content"):
                    think_tok += 1
                    if tt_think is None:
                        tt_think = now
                if delta.get("content"):
                    content_tok += 1
                    if tt_content is None:
                        tt_content = now
            except Exception:
                pass

    return {
        "ttft_think":     tt_think,
        "ttft_content":   tt_content,
        "total_ms":       (time.time() - t0) * 1000,
        "think_tokens":   think_tok,
        "content_tokens": content_tok,
        "finish_reason":  finish_reason,
    }


def fmt(v):
    return f"{v:>7.0f}ms" if v is not None else "    N/A  "


def run_single(base_url, model):
    print("\n" + "="*60)
    print("SINGLE REQUEST — no thinking (/no_think)")
    print("="*60)
    print(f"{'Label':<10} {'TTFT':>9} {'Total':>9} {'Tokens':>7} {'tok/s':>8}  finish")
    print("-"*60)
    for label, prompt, max_tok in SINGLE_PROMPTS:
        r = stream_request(base_url, model, [{"role": "user", "content": prompt}], max_tok)
        tps = 0
        if r["content_tokens"] and r["ttft_content"]:
            t_gen = r["total_ms"] - r["ttft_content"]
            tps = r["content_tokens"] / (t_gen / 1000) if t_gen > 0 else 0
        print(f"{label:<10} {fmt(r['ttft_content'])} {fmt(r['total_ms'])} "
              f"{r['content_tokens']:>7}  {tps:>7.1f}  {r['finish_reason']}")


def run_thinking(base_url, model):
    print("\n" + "="*60)
    print("THINKING MODE (default ON in Qwen3)")
    print("="*60)
    for label, prompt, max_tok in THINKING_PROMPTS:
        print(f"\n[{label}] {prompt[:55]}...")
        r = stream_request(base_url, model, [{"role": "user", "content": prompt}], max_tok)
        think_dur = (r["ttft_content"] or r["total_ms"]) - (r["ttft_think"] or 0)
        print(f"  TTFT think token : {fmt(r['ttft_think'])}")
        print(f"  Thinking duration: {fmt(think_dur)}  ({r['think_tokens']} tokens)")
        if r["ttft_content"]:
            t_gen = r["total_ms"] - r["ttft_content"]
            tps = r["content_tokens"] / (t_gen / 1000) if t_gen > 0 else 0
            print(f"  TTFT answer      : {fmt(r['ttft_content'])}")
            print(f"  Answer speed     : {tps:.1f} tok/s  ({r['content_tokens']} tokens)")
        else:
            print(f"  Answer           : not reached (finish={r['finish_reason']})")
        print(f"  Total latency    : {fmt(r['total_ms'])}")


def run_concurrent(base_url, model):
    print("\n" + "="*60)
    print("CONCURRENT — 4 requests in parallel")
    print("="*60)
    pid = get_server_pid()
    rss_before = get_rss_mb(pid) if pid else 0

    results = [None] * 4

    def worker(idx):
        results[idx] = stream_request(
            base_url, model,
            [{"role": "user", "content": CONCURRENT_PROMPTS[idx]}],
            max_tokens=150,
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    t_wall = time.time()
    for t in threads: t.start()

    peak_rss = rss_before
    while any(t.is_alive() for t in threads):
        rss = get_rss_mb(pid) if pid else 0
        if rss > peak_rss: peak_rss = rss
        time.sleep(0.3)
    for t in threads: t.join()
    wall_ms = (time.time() - t_wall) * 1000

    total_tokens = sum(r["content_tokens"] for r in results if r)
    agg_tps = total_tokens / (wall_ms / 1000)

    print(f"{'Req':<5} {'TTFT':>9} {'Total':>9} {'Tokens':>7} {'tok/s':>8}")
    print("-"*50)
    ttfts, totals = [], []
    for i, r in enumerate(results):
        if not r: continue
        t_gen = r["total_ms"] - (r["ttft_content"] or r["total_ms"])
        tps = r["content_tokens"] / (t_gen / 1000) if t_gen > 0 and r["content_tokens"] > 0 else 0
        print(f"  {i+1:<3} {fmt(r['ttft_content'])} {fmt(r['total_ms'])} "
              f"{r['content_tokens']:>7}  {tps:>7.1f}")
        if r["ttft_content"]: ttfts.append(r["ttft_content"])
        totals.append(r["total_ms"])
    print("-"*50)
    avg_ttft  = sum(ttfts)  / len(ttfts)  if ttfts  else 0
    avg_total = sum(totals) / len(totals) if totals else 0
    print(f"  avg {fmt(avg_ttft)} {fmt(avg_total)} {total_tokens:>7}  {agg_tps:>7.1f}")
    print(f"\nWall clock (all 4): {wall_ms:.0f} ms")
    print(f"Aggregate tok/s:    {agg_tps:.1f}")
    if pid:
        print(f"\nRAM idle:  {rss_before:.0f} MB")
        print(f"RAM peak:  {peak_rss:.0f} MB  (+{peak_rss - rss_before:.0f} MB during load)")


def run_memory(base_url):
    pid = get_server_pid()
    print("\n" + "="*60)
    print("MEMORY USAGE")
    print("="*60)
    if not pid:
        print("  Server process not found")
        return
    fields = ["VmPeak", "VmSize", "VmRSS", "RssAnon", "VmSwap"]
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                key = line.split(":")[0]
                if key in fields:
                    val_kb = int(line.split()[1])
                    print(f"  {key:<12}: {val_kb/1024:>8.0f} MB")
    except Exception as e:
        print(f"  Error: {e}")
    print()
    for l in os.popen("free -h").read().splitlines():
        print(" ", l)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host",       default=DEFAULT_HOST)
    p.add_argument("--port",       default=DEFAULT_PORT, type=int)
    p.add_argument("--model",      default=DEFAULT_MODEL)
    p.add_argument("--single",     action="store_true")
    p.add_argument("--thinking",   action="store_true")
    p.add_argument("--concurrent", action="store_true")
    p.add_argument("--memory",     action="store_true")
    args = p.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    run_all  = not any([args.single, args.thinking, args.concurrent, args.memory])

    print(f"Target : {base_url}")
    print(f"Model  : {args.model}")
    if not health_check(base_url):
        print(f"\nERROR: Server not reachable — start with: bash model/llama-server/start-llama.sh")
        sys.exit(1)
    print("Health : OK")

    if run_all or args.single:     run_single(base_url, args.model)
    if run_all or args.thinking:   run_thinking(base_url, args.model)
    if run_all or args.concurrent: run_concurrent(base_url, args.model)
    if run_all or args.memory:     run_memory(base_url)

    print("\n" + "="*60)
    print("Done.")


if __name__ == "__main__":
    main()
