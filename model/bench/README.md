# Serving benchmark

`model/eval` grades the answer. This grades what the answer cost to produce, on
the same prompts. Three numbers per role, because three different optimisations
move three different numbers and the only point of measuring first is to know
which one to buy.

| Number | What moves it | What does **not** move it |
|---|---|---|
| `ttft_ms` | prompt size — context selection, compression, prefix caching, chunked prefill | quantisation, speculative decoding |
| `decode_tps` | weights and kernels — AWQ/FP8, speculative decoding, CUDA graphs | prompt size |
| `throughput_rps` at concurrency N | batching and KV-cache room — `--max-num-seqs`, `--gpu-memory-utilization`, context length | anything per-request |

A change that improves one and is sold as improving the others is the failure
mode this exists to catch. LoRA moves none of them.

## The profiles

One per LLM role the product runs, built from the *real* system prompt file that
role loads and the *real* `max_tokens` / `temperature` its call site passes —
both cited in `profiles.py`. Edit `planner_system.txt` and the next run says what
it cost.

| Profile | Alias → tier | Out | Where it sits |
|---|---|---|---|
| `guardrail` | `guardrail` → fast | 64 | every request, blocking |
| `planner` | `planner` → fast | 256 | every request, blocking |
| `judge` | `judge` → fast | 128 | after the answer |
| `text_to_sql` | `text_to_sql` → main | 512 | data path |
| `responder` | `responder` → main | 1000 | the streamed answer |
| `orchestrator-planner` | `orchestrator-planner` → main | 256 | multi-agent path |

The user turn rotates per request while the system prefix stays byte-identical —
the shape production sends, and therefore the prefix-cache hit rate production
gets. Holding the user turn fixed too would report a hit rate no real traffic
reaches, which is the standard way a serving benchmark flatters itself.

The corpus behind those user turns is `model/eval/cases/*.jsonl`, not a second
dataset. It is Vietnamese, and Vietnamese costs meaningfully more tokens per
character than the English a generic benchmark pads with — on this product that
difference is the measurement, not a detail.

## Running it

```bash
make bench                                        # every profile, concurrency 1 and 4
make bench BENCH_ARGS="--concurrency 1,4,8,16 --repeat 16 --out bench.json"
make bench BENCH_ARGS="--profile responder --target litellm"
make bench BENCH_ARGS="--force-output"            # comparable decode speed
```

`--out` is written inside the repo (the container mounts nothing else).

`--force-output` makes every request generate exactly `max_tokens` (vLLM's
`min_tokens`). Without it the judge emits about twelve tokens and its
`decode_tps` is mostly noise; with it the number is comparable between runs.
It costs realism in *content*, never in cost-per-token — so use it to compare
decode, and leave it off to see the workload as the product actually runs it.
The report records which way it ran.

`--target direct` (the default) hits the vLLM tier itself; `--target litellm`
goes through the proxy, which is what the services see. **Run both and subtract**
— that difference is the proxy's cost, and there is no other way to attribute it.

Both tiers share one A100, so scenarios run one at a time. Two sweeps in
parallel measure the interference, not either tier.

## The prefix cache outlives the run

vLLM keeps its cache between runs, so a second `make bench` at the default
offset replays the first run's prompts and reports a hit rate it did not earn —
on the responder path that is the difference between ~6% and ~99%. Pass
`--variant-offset N` to start on prompts the server has not seen. The report
records the offset it used, so a comparison between two reports at the same
offset is a comparison of warm caches, which is fine as long as nobody reads it
as cold.

## Reading the report

`ttft p50` far above `ttft p95 − ttft p50` means prefill is dominated by prompt
size, not by queueing: fix it with context, not with hardware.

`hit%` is vLLM's prefix-cache hit rate **for that scenario** (a delta across
`/metrics`, not the server's lifetime average). A low number on a role with a
large fixed system prompt means the prefix is not actually shared — usually
something per-request was placed before it.

`rps` that stays flat from concurrency 1 to 8 means the server is not batching.
No per-request tuning fixes that.

`preemptions` non-zero means vLLM ran out of KV room and evicted running
requests. Every latency number in that scenario is a number about a saturated
server; re-run it lower, or raise `--gpu-memory-utilization` / lower
`--max-model-len`, before concluding anything about the model.

## The gate

```bash
make bench BENCH_ARGS="--force-output --out bench-report.json"
make bench-gate GATE_ARGS="--report bench-report.json"
```

Three exit codes, because a serving gate has a third answer a correctness gate
does not: `0` passed, `1` a budget was missed, `2` a budget could not be judged.
Inconclusive is not a softer failure — a scenario measured while the deployed
stack had a request in flight is partly somebody else's latency, and the fix is
to re-run on a quiet server, not to widen a bound.

The budgets in [`thresholds.json`](thresholds.json) were derived from four
repeated baseline runs, and what is *not* gated is argued there at length. In
short: the same four runs measured TTFT at concurrency 8 anywhere between 62ms
and 4025ms, so latency-under-load is measured, reported and deliberately left
unjudged. What is stable enough to gate is prompt size (deterministic), decode
speed at concurrency 1 under `--force-output` (varied under 1%), and the
prefix-cache hit rate.

Recorded baselines live in [`baselines/`](baselines/), together with what the
LiteLLM proxy costs. Ad-hoc reports are gitignored; kept ones are not.

## End to end

`run.py` measures single calls. It cannot say how many of them one user
question triggers, which is the number that decides routing and cascade work.
[`e2e.py`](e2e.py) asks the deployed agent a real question, times the SSE stream
from outside, then reads back the `AgentTraces` rows that turn wrote — the
product already records `tool_name` and `latency_ms` per step, so nothing here
re-implements the graph.

```bash
export BENCH_AGENT_URL=http://<node>:30240
export BENCH_AGENT_ID=<uuid> BENCH_TENANT_ID=<uuid> GATEWAY_KEY=<key>
make bench-e2e
```

Read `unaccounted_ms` first: wall clock minus the sum of the recorded stages.
Large means the time is going somewhere no stage records; small means the
stages are the whole story and the lever is fewer or cheaper ones. Credentials
come from the environment and never reach the report.
