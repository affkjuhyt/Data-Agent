# Agent end-to-end — bring-up, APIs, and performance measurement

The whole agent turn: a user query → **orchestrator** (input-refine + guardrail +
route + Tree-of-Thoughts planner) → **workflow-runtime** (per-agent planner →
retrieval/tools → responder → judge → suggestion) → **sandbox-executor** (SQL/code
tools) + **KB** + **LLM** (via **litellm** → the host ollama). It is **synchronous
over an SSE stream** — no run_id/poll; the turn is done at `data: [DONE]`.

## 1. Bring it up (local)

Everything runs offline against the host **ollama** (`:11434`). LiteLLM is pointed
at it with a wildcard model in `infra/docker-compose.agent-test.yml` +
`infra/litellm/config.agent-local.yaml`.

```bash
# 1. seed a published dev agent (Agents table is empty on a fresh DB)
docker exec -i dataagent-postgres psql -U admin -d dataagent < scripts/seed_dev_agent.sql

# 2. bring up the agent tier (base + app + local-LLM overlay). NOT app.yml's
#    kb-backend — it collides with the kb_e2e stack on :8050.
docker compose -f infra/docker-compose.yml -f infra/docker-compose.app.yml \
  -f infra/docker-compose.agent-test.yml up -d --build \
  litellm orchestrator workflow-runtime memory-consumer

# (optional, only for SQL/code tools) sandbox-executor needs its runner image:
#   docker build -t sandbox-runner:latest services/sandbox-executor/sandbox_runner/
#   docker compose ... up -d sandbox-executor openbao
```

Host ports: **orchestrator `:8003`**, workflow-runtime `:8001`, litellm `:4000`.
No auth token — dev identity comes from forged headers.

## 2. APIs (Postman: `infra/agent-e2e.postman_collection.json`)

Headers on every call: `X-Tenant-Id: a0000000-0000-0000-0000-000000000001`,
`X-User-ID: 11111111-…`, `X-User-Roles: platform-admin,tenant-admin,engineer`.

| # | Method + path | Purpose |
|---|---|---|
| 0 | `GET :8003/api/orchestrate/health` | liveness |
| 1 | `POST :8003/api/orchestrate/stream` | **the agent turn** (SSE). Body `{query, user_id, agent_id}`. `agent_id` set → single-agent (predictable); omit → multi-agent ToT planner (heavier). Frames: `{"__session_id":…}`, text tokens `"…"`, `{"__suggestions":…}`/`{"__chart":…}`/`{"__clarification":…}`, then `[DONE]`. |
| 2 | `POST :8003/api/orchestrate/stream` (no `agent_id`) | multi-agent worst-case cost |
| 3 | `POST :8003/api/orchestrate/resume` | HITL — only after a `{"__clarification":…}` frame; JSON, not SSE. Body `{session_id, clarification}`. |
| 4 | `POST :8001/api/conversations/run` | downstream single-agent executor (isolate agent-exec latency from the orchestrator layer). |
| 5 | `POST :4000/v1/chat/completions` | the LLM tier (bottleneck). `Authorization: Bearer sk-dev`. |

Minimal turn:
```bash
curl -N -X POST http://localhost:8003/api/orchestrate/stream \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-Id: a0000000-0000-0000-0000-000000000001' \
  -H 'X-User-ID: 11111111-1111-1111-1111-111111111111' \
  -H 'X-User-Roles: platform-admin,tenant-admin,engineer' \
  -d '{"query":"Quyền của chủ thể dữ liệu gồm những gì","agent_id":"00000000-0000-0000-0000-000000000030"}'
```

## 3. Load test (`scripts/loadtest/agent-e2e.js`, k6)

One full turn per iteration; k6 reads the whole SSE body so `agent_turn_ms` = the
end-to-end turn latency; "done" = body contains `[DONE]`.

```bash
VUS=3 DURATION=75s THINK=3 k6 run scripts/loadtest/agent-e2e.js
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
