# Recorded baselines

A file here is a serving measurement someone decided to keep, so that a later
"this made it faster" has something to be faster *than*. Ad-hoc runs are
gitignored; these are not.

Name them `<date>-<what-was-measured>.json` and, when you add one, say in the
commit what the machine was doing at the time. A baseline recorded while the
warehouse was backfilling is not wrong, it is just a different measurement, and
six months later nobody can tell which one it was.

Re-record after anything that changes the serving picture: a vLLM flag, a
quantisation, a model swap, a re-pointed LiteLLM alias, a GPU. Do not re-record
to make a number look better.

## What is here

| File | Run |
|---|---|
| `2026-09-22-a100-40gb-workload.json` | the workload as the product runs it — natural output lengths, concurrency 1/4/8 |
| `2026-09-22-a100-40gb-decode.json` | the same profiles with `--force-output`, so decode speed is comparable |
| `2026-09-22-litellm-proxy.json` | through the deployed LiteLLM proxy (`--target litellm`) |
| `2026-09-22-direct-paired-with-proxy.json` | the matched direct run, for subtracting |

## What the proxy costs

The last two are a pair: same concurrencies, same request count, and each at a
variant offset the server had not seen, so both paid a cold prefix cache. The
difference between them is LiteLLM.

| | median added TTFT | range |
|---|---|---|
| concurrency 1 | **+13 ms** per call | +9 to +15 ms |
| concurrency 8 | **+33 ms** per call | −8 to +63 ms |

Decode speed is untouched, as expected — the proxy is in the connection path,
not the token path.

Thirteen milliseconds is nothing next to a 1000-token answer. It is not nothing
next to a guardrail call whose whole cost is 26ms: on the fast tier the proxy
is roughly half the call. A turn that makes several short fast-tier calls in
sequence pays that tax once per call, which is the argument for counting those
calls — see `model/bench/e2e.py`.

The proxy run has no `hit%`: LiteLLM does not expose vLLM's `/metrics`, so the
harness reports the cache as unmeasured rather than as zero, and
`model/bench/gate.py` calls cache budgets INCONCLUSIVE on such a report instead
of passing them.

Both against Qwen3-14B-AWQ (`agent-main`, 16k ctx) and Qwen3-4B-AWQ
(`agent-fast`, 8k ctx) on one A100 40GB at 10.10.55.101, measured directly
against vLLM rather than through LiteLLM. The GPU also serves the deployed
stack: one scenario in the workload run is flagged `contended`, and its numbers
include somebody else's request.
