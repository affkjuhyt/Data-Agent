# Agent end-to-end — bring-up, APIs, and performance measurement

The whole agent turn: a user query → **orchestrator** (input-refine + guardrail +
route + Tree-of-Thoughts planner) → **workflow-runtime** (per-agent planner →
retrieval/tools → responder → judge → suggestion) → **sandbox-executor** (SQL/code
tools) + **KB** + **LLM** (via **litellm** → the host ollama). It is **synchronous
over an SSE stream** — no run_id/poll; the turn is done at `data: [DONE]`.

## 1. Bring it up (local)

Everything runs offline against the host **ollama** (`:11434`). LiteLLM is pointed
at it with a wildcard model in `deploy/docker/docker-compose.agent-test.yml` +
`deploy/docker/litellm/config.agent-local.yaml`.

```bash
# 1. seed a published demo agent (Agents table is empty on a fresh DB) — same seed the
#    E2E runner and the Ansible `seed` role use (agent `e2e-sql-agent`, metrics, fact_sales):
docker exec -i dataagent-postgres psql -U admin -d dataagent < tests/e2e/seed.sql
docker exec -i dataagent-mongo mongosh --quiet < tests/e2e/seed-flow.js

# 2. bring up the agent tier (base + app + local-LLM overlay). NOT app.yml's
#    kb-backend — it collides with the kb-e2e stack on :8050.
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.app.yml \
  -f deploy/docker/docker-compose.agent-test.yml up -d --build \
  litellm orchestrator workflow-runtime platform-workers-memory

# (optional, only for SQL/code tools) sandbox-executor needs its runner image:
#   docker build -t sandbox-runner:latest backend/services/sandbox-executor/sandbox_runner/
#   docker compose ... up -d sandbox-executor openbao
```

Host ports: **Kong `:8000`**, litellm `:4000`. The orchestrator (`8003`) and
workflow-runtime (`8001`) are **not published** — they are reachable only through
Kong, which verifies the Keycloak JWT and injects the identity headers plus the
shared `X-Gateway-Key`. Forged identity headers no longer work: both services
verify their caller (`backend/libs/shared/auth.py`), and the orchestrator authenticates
to workflow-runtime with a signed service assertion.

Get a token the same way the KB stack does, then send it as `Authorization:
Bearer $TOKEN` to `:8000`:

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token   -d grant_type=password -d client_id=aeroflow-frontend   -d username=helios-engineer -d password='H3lios@Eng001!' | jq -r .access_token)
```

Requires `GATEWAY_KEY` and `SERVICE_AUTH_SECRET` set in `.env` (same value across
services) and `kong-setup.sh` re-run after changing `GATEWAY_KEY`.

## 2. APIs (Postman: `docs/postman/agent-e2e.postman_collection.json`)

Header on every call: `Authorization: Bearer $TOKEN`. Identity headers are
**injected by Kong from the token** — sending your own is pointless (the plugin
clears them) and reaching a service port directly is no longer possible.

All paths below go through Kong on `:8000`, not the service port.

| # | Method + path | Purpose |
|---|---|---|
| 0 | `GET :8000/api/orchestrate/health` | liveness |
| 1 | `POST :8000/api/orchestrate/stream` | **the agent turn** (SSE). Body `{query, user_id, agent_id}`. `agent_id` set → single-agent (predictable); omit → multi-agent ToT planner (heavier). Frames: `{"__session_id":…}`, text tokens `"…"`, `{"__suggestions":…}`/`{"__chart":…}`/`{"__clarification":…}`, then `[DONE]`. |
| 2 | `POST :8000/api/orchestrate/stream` (no `agent_id`) | multi-agent worst-case cost |
| 3 | `POST :8000/api/orchestrate/resume` | HITL — only after a `{"__clarification":…}` frame; JSON, not SSE. Body `{session_id, clarification}`. |
| 4 | `POST :8000/api/conversations/run` | downstream single-agent executor (isolate agent-exec latency from the orchestrator layer). |
| 5 | `POST :4000/v1/chat/completions` | the LLM tier (bottleneck). `Authorization: Bearer sk-dev`. |

Minimal turn:
```bash
curl -N -X POST http://localhost:8000/api/orchestrate/stream \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-Id: a0000000-0000-0000-0000-000000000001' \
  -H 'X-User-ID: 11111111-1111-1111-1111-111111111111' \
  -H 'X-User-Roles: platform-admin,tenant-admin,engineer' \
  -d '{"query":"Quyền của chủ thể dữ liệu gồm những gì","agent_id":"00000000-0000-0000-0000-000000000030"}'
```

## 3. Load test (`tests/loadtest/agent-e2e.js`, k6)

One full turn per iteration; k6 reads the whole SSE body so `agent_turn_ms` = the
end-to-end turn latency; "done" = body contains `[DONE]`.

```bash
VUS=3 DURATION=75s THINK=3 k6 run tests/loadtest/agent-e2e.js
# MULTI=1 → omit agent_id (multi-agent ToT path). AGENT_ID / TENANT / BASE_URL overridable.
```

## 4. Measured (this M3 laptop, ollama qwen2.5:3b behind litellm)

| Concurrency | turn latency (p50 / p95 / max) | TTFB | errors |
|---|---|---|---|
| 1 turn | ~9–15 s | ~30 ms | 0 |
| **3 concurrent (k6)** | **24.7 s / 38.3 s / 45 s** | 36 ms | **0 %** (11/11 reached `[DONE]`) |

**Reading:** each turn fires several LLM calls (input-refine, guardrail, planner,
responder, judge, suggestion). **TTFB is instant** (~36 ms — the `__session_id`
frame) but the full turn is **LLM-bound**: 1 turn ≈ 9–15 s, and at 3 concurrent
turns it climbs to ~25 s because they share the **single GPU** (same ceiling the KB
tier hit — see KB-SCALE-10K.md). Orchestration overhead itself is negligible vs the
LLM. **Agent throughput scales with GPU capacity**, not orchestrator/worker count;
a GPU pool (or a smaller/faster model per step) is the lever. Note: with the tiny
local model the guardrail/responder quality is poor (guardrail false-positives on
1.5b; empty responder text on 3b) — the numbers measure the *pipeline latency*, not
answer quality; a production LLM removes that.

## 5. Stability — why calls hang, and the fix

Symptom: `/api/orchestrate/stream` emits the `__session_id` frame then **hangs 60 s+**
with no `[DONE]`. Two root causes, both about the **single shared GPU**:

1. **ollama juggling >1 model on one GPU.** The agent uses `qwen2.5:3b` (litellm),
   but the **llm-exporter** monitoring probe and the KB `model-service` keep
   `qwen2.5:1.5b` resident. With two models loaded, ollama **swap-thrashes** the GPU
   → each LLM call jumps from ~2 s to ~15–20 s → a multi-call turn stretches past the
   client timeout and looks hung. **Fix: keep exactly one model resident** — stop
   the other consumers (`docker stop llm-exporter kb-model-service`) and unload the
   stray model (`curl :11434/api/generate -d '{"model":"qwen2.5:1.5b","keep_alive":0}'`),
   or in prod give each tier its own GPU / a GPU pool.
2. **OTEL tracing to `jaeger:4317` (not running).** Both services export spans there
   and spam "export timeout"; disabled with `OTEL_SDK_DISABLED=true` (in
   `docker-compose.agent-test.yml`). Bring up jaeger instead if you want traces.

After both fixes: **single turn ~5 s, 3 concurrent ~10.5 s, 0 hangs** (was 60 s+).
The lesson: the agent turn is fine; the single GPU is a **shared contention point** —
any second ollama consumer (KB, the monitoring probe, ingestion) starves it and the
agent appears to hang. Keep one model per GPU, or scale the GPU tier.

### The snowball: abandoned LLM calls (why retrying makes it worse)

A third, nastier cause. When a client times out / disconnects mid-turn, the
in-flight LLM generations are **not cancelled** — ollama keeps generating, litellm
keeps the request. Retrying stacks more. Observed after a burst of test calls: **41
open connections, 27 queued /chat requests, each ollama generation 40–80 s**. With
`OLLAMA_NUM_PARALLEL=4` only 4 run; the rest queue (`OLLAMA_MAX_QUEUE=512`), so every
new turn waits behind the backlog → hangs. **Retrying a hung call adds load and
cancels nothing → it snowballs** ("càng gọi càng treo").

Immediate remedy: clear the backlog — `pkill -f 'ollama serve' && ollama serve` (or
restart the ollama container) + `docker restart dataagent-litellm`. Then turns are
~4–5 s again.

Production stability fixes (the real ask):
1. **Cancel-on-disconnect (DONE).** `stream.py` wraps the SSE generator so a client
   disconnect `aclose()`s it → GeneratorExit propagates into the LangGraph stream →
   the in-flight LLM node task is cancelled (httpx connection to litellm closes) and
   the admission slot is released. Verified: disconnecting mid-turn frees the slot
   immediately (a following turn gets 200, not 429).
2. **Admission control (DONE).** `ORCHESTRATOR_MAX_CONCURRENT_TURNS` (default 8; set
   to **2** on this single-GPU box in `docker-compose.agent-test.yml`) caps
   concurrent turns; over the cap the orchestrator sheds **429 + Retry-After**
   instead of piling onto the GPU — same backpressure pattern as the KB gateway.
   Verified: 5 concurrent at cap 2 → 2×200, 3×429.
3. **Short per-call LLM timeout + no retries (DONE).** `request_timeout: 30`,
   `num_retries: 0` in `config.agent-local.yaml` — a stuck call frees its GPU slot
   fast instead of holding it 120 s.
4. **Client:** bounded timeout + backoff, never blind-retry a slow turn.
5. Ultimately throughput is **GPU-bound** — a faster model or a GPU pool drains the
   queue so a transient backlog doesn't wedge everything. Tune
   `ORCHESTRATOR_MAX_CONCURRENT_TURNS` to the LLM tier's real parallelism.

## 6. Evaluation + product readiness

`tests/e2e/agent_eval.py` runs a 10-case labelled query set (factual, summary,
comparison, list, short, greeting, out-of-domain, ambiguous, prompt-injection,
data-query) and scores each turn on speed + behavioural quality.

Two real defects were found and fixed during evaluation (both made every answer
**empty** while the turn still `[DONE]`d in ~4.6 s):

1. **`runtime_writer` role lacked SELECT** on the agent-config tables → "permission
   denied" in workflow-runtime. Fixed by GRANT (in `tests/e2e/seed.sql`).
2. **`LLMProviders.model_id` had no provider prefix** ("responder") → the
   workflow-runtime responder calls the litellm *SDK* directly, which raised "LLM
   Provider NOT provided". Fixed to `openai/responder` so the SDK routes to the
   litellm proxy → ollama.

After the fixes (ollama qwen2.5:3b behind litellm):

| Dimension | Result |
|---|---|
| Correctness | **8/10 PASS** — relevant, coherent answers (1.1–1.3k chars on detailed Qs); 0 empty |
| Reliability | **0 hangs**, 100 % `[DONE]`, admission-shed 429 under load |
| Speed — TTFB | **~20–50 ms** (first token) — streaming UX feels instant |
| Speed — full turn | **p50 34.7 s / p95 48 s** — a 3B model generating a ~1k-token answer on one M3 GPU (responder alone ≈ 18 s at ~55 tok/s), plus planner/judge/suggestion calls |
| Guardrail | **Weak** — prompt-injection not blocked, ambiguous not clarified (tunable: guardrail config/model) |
| Throughput | GPU-bound — ~2 concurrent real-answer turns; scales with GPU |

**Verdict:** the **architecture + pipeline are product-grade** (reliable, correct,
streaming, self-protecting). The gap to "product" is the **LLM tier**: a 3B model on
one laptop GPU makes a *complete* answer take 30–48 s. Levers to interactive speed:
(a) a hosted/faster LLM or GPU pool → 2–5 s turns; (b) cap responder `max_tokens`
(1k-token answers are long); (c) per-step model routing — a tiny model for
guardrail/planner, a strong one for the responder; (d) lean on streaming (TTFB is
already instant). Guardrail quality is a separate, tunable track (config + a better
guardrail model / rules).
