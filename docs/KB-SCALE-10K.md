# KB @ 10,000 CCU — engineering brief

How to make the Knowledge-Base **retrieval** tier sustain 10,000 concurrent
users. This is the problem statement + the sizing math + the concrete changes to
implement. Grounded in measured per-request costs (this stack, 2026-07).

> **Note (30-08-2026):** `kb-gateway` and `kb-compute` were removed — rate limiting is now
> Kong's `rate-limiting` plugin on `kb-backend` (`deploy/docker/kong/kong-setup.sh` §10) and
> RRF fusion runs in-process in `kb_backend` (`rrf_merge`). References below to those
> services are historical measurements, kept as-is.

---

## 0. First, turn "10,000 CCU" into numbers (do this before anything)

**CCU = concurrent user sessions, NOT concurrent requests.** With think time,
Little's Law converts it:

```
arrival_rate (RPS) = CCU / think_time
in-flight requests = arrival_rate × service_time
```

| Think time | RPS (10k CCU) | In-flight @150ms (cached) | In-flight @900ms (cold) |
|---|---|---|---|
| 30 s (browsing) | 333 | ~50 | ~300 |
| 15 s (moderate) | 667 | ~100 | ~600 |
| 10 s (active) | 1,000 | ~150 | ~900 |
| 5 s (burst) | 2,000 | ~300 | ~1,800 |

**Design target: 1,000 RPS sustained, 2,000 RPS peak** (10k CCU, moderate-active).
That is very achievable — the number to fear is not 10k, it's the **request mix**.

### Decide the mix (the cost differs 100×)

| Class | What it hits | Cost | Scales by |
|---|---|---|---|
| **Retrieval** (`/retrieve`) | embed + Qdrant + rerank + Neo4j | **embed-bound** | cache + GPU/replicas |
| **CRUD** (list/doc/kb) | Postgres + Qdrant | cheap (~18 ms) | stateless replicas |
| **Ingestion** (upload/promote) | MinIO + PG + **Kafka** | async, off the hot path | queue + workers |
| **RAG answer generation** | **LLM** | **very expensive** | GPU fleet (separate) |

> **Scope this brief to retrieval + CRUD.** LightRAG `/retrieve` returns ranked
> chunks; it does **not** generate an answer. If 10k users each also need an
> LLM-generated answer, that's a *separate* vLLM-cluster problem (see §7) an
> order of magnitude more expensive — keep it decoupled.

---

## 1. Measured per-request cost (where the money goes)

Cold `/retrieve` = **897 ms total**, broken down:

| Stage | ms | % | Tier |
|---|---|---|---|
| **embed_query** | **594** | **66 %** | **model-service (CPU) ← THE bottleneck** |
| doc_narrow (Qdrant) | 105 | 12 % | Qdrant |
| qdrant_pass1 | 74 | 8 % | Qdrant |
| cache lookup+store | 79 | 9 % | Redis/Qdrant |
| graph_expand | 28 | 3 % | Neo4j |
| rerank / hydrate / rest | ~17 | 2 % | model-service / PG |

Warm (query cache hit) = **149 ms**. CRUD = **18 ms**.

**Takeaway: retrieval is embedding-bound.** Everything else is already fast and
horizontally scalable. So the plan is, in order of impact:

1. **Cache the embedding + the result** → most requests never embed.
2. **Give embedding a GPU** (or many CPU replicas) for the cache misses.
3. **Scale the stateless tiers horizontally** behind a load balancer.

---

## 2. The three levers (ranked by impact)

### Lever 1 — Caching (kills 70–95 % of the expensive work) ★ biggest
- **Query-result cache** (already exists: `source:"cache"`, 149 ms). Make it
  robust: key = `hash(tenant_id, kb_id, normalized_query, filters)`, TTL 5–15 min,
  **invalidate on ingest** (publish an invalidation on `kb.promote.gold`). Store in
  Redis, not Qdrant, for the hot path.
- **Embedding cache** (NEW, do this): key = `hash(normalized_query)` → vector.
  Cross-tenant safe (embeddings aren't tenant data). A huge fraction of queries
  repeat ("search box" traffic is Zipfian) → skips the 594 ms embed.
- Effect: at 80 % combined hit rate, only **20 %** of RPS reaches embed.

### Lever 2 — Embedding throughput for the misses
- **1 CPU replica ≈ 1.7 embed/s** (594 ms each). **1 GPU (T4/A10) ≈ 500–1000
  embed/s batched.**
- At 1,000 RPS × 20 % miss = **200 embed/s needed** →
  - GPU: **1 GPU** covers it (with batching); 2 for HA.
  - CPU-only: ~120 model-service replicas — don't; use GPU or raise cache hit.
- Enable **dynamic batching** in model-service (collect embed requests for ~5 ms,
  run as one batch) — 5–10× GPU efficiency.

### Lever 3 — Async + degradation
- Ingestion + generation already go through **Kafka** → never on the user hot path.
- Add **graceful degradation** under load: env/timeout flags to skip
  `graph_expand` (Neo4j) and `rerank` when p95 rises — they're 3 % + 0.3 % of value
  but add tail latency and a dependency. Return pass-1 + cache results.

---

## 3. Tier-by-tier sizing (the "bài toán")

Target: **2,000 RPS peak**, retrieval:CRUD ≈ 60:40, 80 % cache hit.

| Tier | Throughput / instance | Replicas @ 2k RPS | Type | Notes |
|---|---|---|---|---|
| **Kong** | ~10k RPS/node | **2** | stateless | JWT/JWKS verify; keep |
| **kb-backend (Py async)** | ~300–500 RPS/replica (I/O-bound) | **6–8** | stateless + HPA | 2 vCPU / 512 MB each |
| **model-service (embed/rerank)** | GPU ~800/s batched · CPU ~1.7/s | **2 GPU** (or heavy cache) | stateless + HPA | **the sizing crux**; dynamic batching ON |
| **Qdrant** | ~1–3k QPS/node | **3 nodes** (replicated + sharded) | stateful | HNSW; replicate for read QPS |
| **Postgres** | writes 1 primary | **1 primary + 2 read replicas** | stateful | **PgBouncer** (transaction pooling) in front; RLS reads hit replicas |
| **Neo4j** | reads scale w/ replicas | **1 leader + 1–2 read replicas** | stateful | or make graph-expand optional (§2) |
| **Redis** | 100k+ ops/s | **1 primary + 1 replica** | stateful | query + embedding cache |
| **Kafka** | — | **3 brokers** | stateful | ingestion only, not user CCU |
| **kb-ingestion-worker (Py)** | N docs/min | **scale by backlog** | queue consumer | async; not user CCU |

**Connection math (the classic 10k-CCU killer):** never let 10k users translate
to 10k DB connections. kb-backend async pool (~20 conns/replica) × 8 replicas =
160 → **PgBouncer** multiplexes to ~50 real Postgres connections. Qdrant/Neo4j
likewise pooled per replica.

---

## 4. Architecture changes to implement (checklist)

**Already done in this repo:**
- [x] ~~`kb-gateway` / `kb-compute` (Go)~~ — removed 30-08-2026; Kong rate-limiting on
  kb-backend + in-process RRF replace them.
- [x] Kafka-decoupled ingestion; query cache; stateless kb-backend.

**To build (in priority order):**
1. [x] **Embedding cache** (LRU, hash→vector) + **dynamic batching** in
   model-service (`backends/embed_service.py`, exported on `/metrics`). Measured: repeated
   query embed **140 ms → 1.4 ms (100×)**; 20 concurrent unique embeds coalesced
   into **2 encode() calls (~10× fewer forward passes)**. L2 Redis drops in behind
   the same interface for multi-replica sharing.
2. [x] **Result-cache hardening**: L0 Redis exact-key cache in front of retrieval
   (`backend/libs/shared/kb_result_cache.py`), tenant-scoped **versioned** key
   (`kbrc:{tenant}:v{ver}:{kb}:{qhash}`), native TTL (900 s), and O(1)
   invalidation — the worker `INCR`s the tenant version on **gold complete**, so
   every cached result is orphaned at once (no SCAN). Best-effort (Redis down ≠
   broken retrieval). Measured: exact-repeat retrieval **688 ms → 2.9 ms (237×),
   skipping embed entirely**; version bump → cache miss → recompute verified.
   (The in-pipeline Qdrant *semantic* cache stays as an independent L1 with its
   own 24 h TTL; flushing it on gold too is a follow-up.)
3. [x] **PgBouncer** (transaction pooling) — `deploy/k8s/production/database/pgbouncer.yaml`.
   Verified: 20 client connections multiplex onto ~10 real Postgres backends
   (`DEFAULT_POOL_SIZE`). Cutover note (asyncpg `statement_cache_size=0`) is in the
   overlay. Read replicas remain a deploy-topology step (managed PG / k8s).
4. [x] **Qdrant replication** — 2-node distributed cluster in
   `deploy/k8s/production/database/qdrant.yaml` with `QDRANT_CLUSTER_ENABLED=true` and >1 replica. Verified: a
   collection with `shard_number=2, replication_factor=2` keeps 4 shard-copies
   (2 local + 2 remote), a point inserted on node-0 reads back from BOTH nodes,
   and reads survive killing node-1 (which re-syncs on restart). **Use ≥3 nodes in
   prod** — a 2-node Raft loses write quorum when one dies (reads still served).
   Cutover: point `QDRANT_URL` at a cluster node + recreate the `knowledge`
   collection with `replication_factor=2`.
   **Neo4j read replica** — NOT possible on the current **Community** edition
   (5.20.0); causal clustering / read replicas are **Enterprise-only**. Mitigation
   is already shipped: graceful degradation (#5) skips `graph_expand` under load and
   the result cache absorbs repeats — and Neo4j is only ~3% (27 ms) of a retrieval,
   not a 10k-CCU bottleneck. Add read replicas only when moving to Neo4j Enterprise.
5. [x] **Graceful degradation**: `DEGRADE_BUDGET_MS` per-request budget in the
   pipeline — past budget it skips Neo4j `graph_expand` + cross-encoder `rerank`
   and returns RRF order; the skipped steps are surfaced in `degraded[]`.
6. [x] **Per-tenant rate limiting** at kb-gateway (Go token-bucket per
   `X-Tenant-Id`, `KB_GATEWAY_RATE_RPS`; 429 + `Retry-After`). Verified: one tenant
   throttled while another is unaffected.
7. [x] **HPA** — `deploy/k8s/` manifests: Deployment + Service + HorizontalPodAutoscaler
   for kb-gateway (2→10), kb-backend (3→12), model-service (2→8), CPU-target with a
   custom-RPS-metric note. **model-service auto-detects its device** (`device.py`:
   CUDA > Apple MPS > CPU) so the same image runs
   GPU-less (here) or on a GPU node pool — the manifest only changes scheduling.
   Plus **DDoS/flood protection** at kb-gateway (global + per-IP rate + max-concurrent
   → 429/503, verified: 10 parallel @ max-concurrent=2 → 8×503+2×200).
8. [x] **Observability** — Prometheus + Grafana (`deploy/k8s/production/service/prometheus.yaml` + `grafana.yaml`,
   ). kb-gateway `/metrics` (Go): RPS by status, latency
   histogram, in-flight, rejections; model-service `/metrics` (Py): embed cache
   hit-rate, batch size, detected device. Provisioned Grafana dashboard **"KB —
   High CCU"** (`http://localhost:3000/d/kb-ccu`, anon admin) — RPS, p95/p50,
   in-flight, 429/503, cache hit-rate, device. Load test:
   `tests/loadtest/ccu-10k.js` (k6) models 10k users with think-time. Verified
   end-to-end: k6 → gateway → Prometheus → live Grafana panels.

---

## 5. Deployment shape (Kubernetes)

- One **GPU node pool** (model-service) + one **general pool** (everything else).
- HPA on the 3 stateless tiers; PodDisruptionBudgets; readiness = the existing
  `/health`.
- Qdrant/Postgres/Neo4j/Kafka/Redis as StatefulSets or managed services
  (managed Postgres + managed Redis strongly recommended — offload the hard
  stateful ops).
- Ingress → Kong (authN + rate-limiting) → kb-backend (Py) → data plane.

---

## 6. Load test to PROVE 10k CCU (don't guess)

Run from a **k8s staging cluster or k6 Cloud**, not a laptop.

- Extend `tests/loadtest/ccu-test.js`: ramp VUs to model 10k CCU **with
  think-time** (`sleep(15±5s)` per iteration → ~667 RPS), realistic mix (60/40),
  and a **cold:warm query ratio** (e.g. 30 % unique queries) so you actually
  exercise embed, not just the cache.
- Stages: 1k → 3k → 6k → 10k VUs, hold 10 min each.
- **Pass criteria:** p95 within SLO at 10k VUs, error < 0.1 %, no tier > 80 %
  saturation, cache hit ≥ target. Find the knee, then size replicas to sit at
  ~60 % of it.

```bash
# 10k CCU model (think-time built into the script; run distributed)
VUS=10000 DURATION=10m THINK=15 k6 run tests/loadtest/ccu-10k.js
```

### Measured on this MacBook M3 Pro (18 GB, no GPU) — 2026-07-19

Full 10k run on one laptop (`VUS=10000 DURATION=60s THINK=15 UNIQUE_PCT=10`),
observed live in Grafana → **KB — High CCU** (`http://localhost:3000/d/kb-ccu`):

| Metric | Value | Read |
|---|---|---|
| Concurrent users | **10,000** (`vus_max`) | real 10k CCU driven locally |
| Throughput | **430 rps** (68,852 reqs) | matches Little's Law for the window |
| Success | **84.7 %** (58,317 / 68,852) | median stays fast under load |
| retrieve median / p95 | **38 ms / 3.97 s** | cache absorbs the bulk; tail blows out at peak |
| crud median / p95 | **73 ms / 4.02 s** | same shape |
| Failures | **15.3 %**, all `EOF` | at absolute peak, **not** 5xx/429 |
| Machine | **49 % free mem, load 4.5** | the laptop itself never thrashed |

**Root cause of the EOF tail (found + fixed):** the gateway's reverse proxy used
Go's default `http.Transport`, which keeps only **`MaxIdleConnsPerHost=2`** idle
connections to kb-backend. Under 10k CCU the proxy churned a fresh TCP connection
per request → ephemeral-port exhaustion → clients saw bare `EOF` (not `429`).
Fixed in `backend/services/kb-gateway/main.go`: pooled transport
(`MaxIdleConnsPerHost=2000`, unlimited `MaxConnsPerHost`), a `502` ErrorHandler so
upstream failures are counted instead of dropped, and gateway RAM 128 MB → 512 MB.

**Same 10k run, after the fix:**

| Metric | Before | After |
|---|---|---|
| Success | 84.7 % | **99.99 %** (5 / 71,338 failed) |
| retrieve median / p95 | 38 ms / 3.97 s | **29 ms / 445 ms** |
| crud median / p95 | 73 ms / 4.02 s | **51 ms / 1.94 s** |
| Throughput | 430 rps | **446 rps** |
| Failures | 15.3 % `EOF` | **0.007 %** |

**Single-laptop verdict:** with the pooled transport, one M3 laptop serves
**10,000 CCU at ~99.99 % success, retrieve p95 445 ms** — the laptop was never the
ceiling; a two-line stdlib default was. Remaining minor tail (crud p95 ~2 s, max
~11 s) comes from the single kb-backend replica + CPU cold-embed; both are removed
in prod by the `deploy/k8s/` HPA (N backend replicas) and a GPU embed node. Optional
graceful shedding (`KB_GATEWAY_*` envs > 0) converts any residual overload into
clean `429/503` instead of latency.

### Backend tier — direct closed-loop sweep (`ccu-test.js`, no think-time)

Hitting kb-backend directly on `:8220` (bypassing the gateway) with N *in-flight*
requests — this measures one replica's raw concurrency ceiling, not CCU:

| Concurrency | rps | retrieve p95 | crud p95 | errors |
|---|---|---|---|---|
| 50 | 1,778 | 28 ms | 65 ms | 0 % |
| 150 | 1,865 | 76 ms | 393 ms | 0 % |
| 300 | 1,917 | 144 ms | 842 ms | 0 % |
| 600 | 1,850 | 311 ms | 1.86 s | 0 % |

**Finding:** throughput **plateaus at ~1,900 rps from concurrency 50 upward** —
one `uvicorn --workers 1` process saturates ~1 core; extra load only queues
(latency climbs linearly, rps flat, **0 % errors** — the async worker never drops).
The **crud/Postgres path degrades faster** than retrieval (cache-hit), so the DB
path is the latency-sensitive one, not retrieval.

**Fix (found + applied):** `uvicorn --workers` is now env-tunable
(`UVICORN_WORKERS`, default 1) in `backend/services/kb_backend/Dockerfile` + `deploy/k8s/production/service/kb-backend.yaml`.
Same VUS=300 sweep with **4 workers**:

| VUS=300 | 1 worker | 4 workers |
|---|---|---|
| Throughput | 1,917 rps | **4,578 rps** (2.4×) |
| retrieve p95 | 144 ms | **66 ms** |
| crud p95 | 842 ms | **140 ms** (6× better) |
| errors | 0 % | 0 % |

**Backend verdict:** one container at 4 workers serves **4,578 rps ≈ 7× the ~670
rps** that 10k CCU needs — the backend tier is *not* the ceiling once it uses more
than one core. Set `UVICORN_WORKERS` = CPU cores per replica; the DB path benefits
most (crud p95 6× better) because it was serialized on the single process's GIL.
Next DB lever if pushed further: the PgBouncer pooler in `deploy/k8s/production/database/pgbouncer.yaml`.

### LLM tier — generation benchmark (`model/llama-server/benchmark_llm.py`, ollama qwen2.5:3b)

Measured on host (Metal), streamed OpenAI-compatible `/v1/chat/completions`:

| Probe | Result | Read |
|---|---|---|
| Single TTFT | **~120–210 ms** | fast first token |
| Single speed | **~55 tok/s** | steady generation |
| Concurrent ×4 aggregate | **~53 tok/s** (= single) | **no concurrency gain** |
| Concurrent ×4 TTFT | 2.6–8.2 s | requests queue behind each other |

**Finding:** the LLM **serialises** — aggregate throughput under 4 parallel
requests equals a single stream (~55 tok/s), because one GPU = one queue. This is
exactly why every CCU test **bypasses the LLM** (canonicalize via front-layer
`entities`): the LLM belongs to ingestion / offline canonicalize, **never the 10k
retrieval hot path**. If an LLM step must be online, it needs its own queue +
autoscaled GPU pool sized by tok/s, budgeted separately from the retrieval tier.

**Now viewable live in Grafana:** a stdlib `backend/services/llm-exporter/` probes the LLM
every 30 s and exposes `/metrics` (`kb_llm_tokens_per_sec`, `kb_llm_single_ttft_ms`,
`kb_llm_concurrent_agg_tps`, …); Prometheus scrapes it and the **"LLM tier"** row on
the *KB — High CCU* dashboard shows TTFT, tok/s (single vs concurrent), and latency.

#### LLM evaluation + optimisation levers

At ~55 tok/s single-stream and **serialised** concurrency, one node does
55 tok/s ÷ ~40 tokens/canonicalize ≈ **1.4 canonicalize/s**. Serving 10k CCU at
670 rps online would need ~480× that — **architecturally impossible per-query**, so
the entities-bypass isn't just an optimisation, it's mandatory. Ranked levers:

1. **Never call the LLM online per query (done) + cache canonicalization.** The
   front-layer `entities` bypass keeps it off the hot path; add a query→entities
   cache (same shape as the result cache) so the few queries that *do* need
   extraction skip the LLM on repeats.
2. **Smaller model for extraction (measured).** Canonicalize/entity-extraction is
   structured, not reasoning — it doesn't need 3B. Warm single-stream speed:

   | Model | tok/s | TTFT | vs 3B |
   |---|---|---|---|
   | qwen2.5:0.5b | ~170 | ~70 ms | **3.1× faster** |
   | qwen2.5:1.5b | ~94 | ~88 ms | 1.7× faster |
   | qwen2.5:3b | ~55 | ~124 ms | baseline |

   Use **0.5b–1.5b for canonicalize**, keep 3b only where extraction quality
   matters (validate quality before switching production ingestion).
3. **Enable real concurrency.** `OLLAMA_NUM_PARALLEL` is unset → ollama picked 1,
   which is why 4 parallel requests serialised (TTFT 2.6–8.2 s). Set
   `OLLAMA_NUM_PARALLEL=4` + `OLLAMA_MAX_QUEUE` for continuous batching so
   concurrent callers interleave instead of queueing head-of-line.
4. **Pin the model** (`OLLAMA_KEEP_ALIVE=-1`). A cold model swap cost ~10 s in the
   sweep (1.5b first call) — pin the canonicalize model so it never reloads.
5. **Cap `max_tokens` + structured/JSON output + stop sequences** for canonicalize
   — entities are short; don't let it generate 150 tokens when 20 will do.
6. **Ingestion = throughput, not latency.** Batch documents through a dedicated
   LLM queue sized by tok/s (backlog ÷ tok/s = drain time); never share that queue
   with any online path. FLASH_ATTENTION=1 is already on (good).

#### Applied + re-measured (levers #2 and #3)

**#3 — `OLLAMA_NUM_PARALLEL=4` (measured effect).** Default was 1, so 4 parallel
requests queued head-of-line. With 4 (4 concurrent, qwen2.5:3b):

| 4 concurrent | NUM_PARALLEL=1 | NUM_PARALLEL=4 |
|---|---|---|
| TTFT (per request) | 0.16–8.2 s | **0.47–0.74 s** (all start ~immediately) |
| Aggregate tok/s | 52.6 | **61.7** (+17 %) |
| Per-request tok/s | ~56 | ~17.5 (shares the GPU) |

Verdict: it **erases head-of-line blocking** (TTFT 8.2 s → 0.7 s — the big win for
concurrent fairness) but only +17 % aggregate, because the M3 GPU is
**compute-bound** — one GPU split 4 ways. On a datacenter GPU with spare compute,
continuous batching pays off much more. Set it on any shared LLM node. *(On this
laptop it's set on a manual `ollama serve`, since `brew services` rewrites the
plist on restart — for a permanent brew setup, use `launchctl setenv` or a wrapper.)*

**#2 — small canonicalize model, wired end-to-end.** `CloudflareQwenChatCaller`
now honours `REMOTE_LLM_MODEL`; model-service's gateway points at host ollama with
**qwen2.5:1.5b**. Verified: retrieve *without* entities → pipeline calls
`/generate` (200) → 5 results in **~1.9 s** (`/generate` alone ~2.0 s). **Quality
caveat:** on Vietnamese extraction 1.5b is noticeably rougher than 3b (3b returned
clean `{"entities":[…]}`, 1.5b a messier object) — pair the small model with
`json_schema` structured output (lever #5) before trusting it in production, or
keep 3b where extraction quality matters.

#### Full re-measurement after the LLM changes (regression check)

Re-ran the whole suite after wiring the LLM. The LLM changes **do not touch the
CCU path** (retrieval bypasses the LLM via `entities`) — confirmed:

- **Backend sweep** (`ccu-test.js`, 1 worker): 50→300 concurrent = ~1,700–1,770
  rps, 0 % errors. No regression.
- **10k gateway, kb-backend at 1 worker: 88.3 % success** — the **crud/Postgres
  path** collapsed (crud p95 → 15 s timeout; 5,759 crud vs 1,017 retrieve
  failures) while retrieve median stayed 90 ms. Reproducible across a gateway
  restart + exporter-off + warm cache, so it's the DB path, not contention.
- **10k gateway, kb-backend at 4 workers: 100.00 % success** (71,940/71,940, 0
  failures), crud p95 **15 s → 112 ms**, retrieve p95 **24 s → 118 ms**, 449 rps.

**Conclusion:** 10k CCU on this box **requires `UVICORN_WORKERS ≥ 4`** — one worker's
crud/Postgres path is the binding constraint at 10k (it's exactly the GIL-serialised
DB weakness from the backend sweep, now hit at full CCU). This supersedes the
earlier "baseline 1 worker": **for the 10k target, run 4 workers per backend
replica.** The gateway transport pool + backend workers together give a clean
10k CCU; neither alone suffices.

### Expert / advanced tests (beyond verify + sweep)

**1. Cold-cache stress (`UNIQUE_PCT=100` — every query a cache miss → real embed
CPU path).** No GPU, so this finds the embed ceiling the result cache normally hides:

| Cold CCU | retrieve median | retrieve p95 | success |
|---|---|---|---|
| 1,000 | 37 ms | 65 ms | 100 % |
| 3,000 | 1.66 s | 3.74 s | 100 % |

Embed (CPU, model-service ~75 %) saturates between **1,000–3,000 concurrent cold
queries** — latency explodes 65 ms → 3.7 s (0 errors, just slow). Warm 10k was
p95 118 ms, so **the result cache is doing the heavy lifting**; the true cold
ceiling is ~1–1.5k CCU. A GPU embed node removes it. (`kb_embed_avg_batch` ≈ 1.4 —
the dynamic batcher barely coalesces query-embeds; it pays off for ingestion, not
one-query-per-request retrieval.)

**2. Stress-to-breakpoint (4 workers, warm, ramp past 10k → find the knee).**

| CCU | success | retrieve p95 | in-flight | served rps |
|---|---|---|---|---|
| 10,000 | **100 %** | 118 ms | 47 | 449 |
| 15,000 | 68 % | ~30 s | 9,116 | 433 |
| 20,000 | 50 % | ~31 s | 13,608 | 446 |

Served throughput plateaus at **~445 rps**; past 10k CCU the offered load exceeds
the drain rate, in-flight explodes (47 → 13,608) and requests time out. **Knee ≈
10–12k CCU.** Caveat: this is the ceiling of **one laptop running k6 + the whole
stack co-located** (load 20+ on 11 cores) — not the architecture's limit; splitting
load-gen off and adding HPA replicas scales past it.

**Proof the ceiling is the co-located load-gen, not the stack.** Re-drove the
*same* gateway + 4-worker backend with a closed-loop generator using **few
connections** (low k6 overhead) instead of 15k VUs:

| Load shape | rps served | retrieve p95 | errors |
|---|---|---|---|
| 15,000 VUs (co-located, load 20+) | 445 | ~30 s | 32 % |
| closed-loop, 100 conns | **3,540** | 31 ms | 0 % |
| closed-loop, 300 conns | **3,573** | 90 ms | 0 % |

The identical stack serves **~3,570 rps — 8× the 445** — once the load generator
stops stealing the machine. At 15 s think-time that's **~53,000 CCU** of request
rate from one laptop's serving path; the 10–12k "knee" was purely k6 co-location.
Real deployments run load off-box and add HPA replicas, multiplying this further.

**3. Rate-limit / DDoS shedding (turn `KB_GATEWAY_*` limits on, drive past them).**
With `GLOBAL_RPS=100, MAX_CONCURRENT=50, IP_RPS=100` and ~130 rps offered:

| Gateway status | share |
|---|---|
| 200 | 84.7 % |
| **429 (shed)** | **15.3 %** |

Overload is shed as **clean, immediate `429`** — no timeouts, no `EOF`, the exact
opposite of the raw-capacity run's dropped sockets. This is the production-correct
failure mode: protect the backend, tell the client to back off. The shed is now
counted by reason in `kb_gateway_rejected_total{reason}` (ratelimit_tenant /
ratelimit_global / ratelimit_ip / overload) — verified matching the 429/503 status
counts — so the Grafana "Rejections" panel reflects rate-limit sheds, not just the
concurrency-cap 503s.

**4. Ingestion throughput (medallion bronze→silver→gold, `tests/loadtest/
ingestion_throughput.sh`).** Unlike the CCU tests, this drives the LLM + embed path
on purpose. N=6 small (1-chunk) docs, single worker:

| Stage | time | note |
|---|---|---|
| bronze (×6, sync) | 0.4 s | MinIO + DB, no LLM |
| silver | 30.9 s | **82 % of wall** — Docling parse + LLM extraction |
| gold | 6.4 s | per-chunk LLM + embed + Qdrant/Neo4j |
| **end-to-end** | **37.7 s** | **≈ 0.16 docs/s (~6.2 s/doc), 6/6 PUBLISHED** |

Ingestion is **throughput-bound by the single Kafka worker + model-service
`/generate`**, not by HTTP (promote returns instantly; the worker does the work
async). Silver (parse + extract) dominates. These are tiny docs — real
multi-chunk documents scale the LLM/embed work per doc, so 0.16 docs/s is an
optimistic floor. **Scale path:** ingestion is embarrassingly parallel across
documents — add Kafka partitions + more `kb-ingestion-worker` replicas
(`deploy/k8s/production/job/kafka-topics.yaml` + `--scale`) to multiply docs/sec; it never shares a queue with the
online retrieval path, so it can't hurt CCU.

#### Scaled + large-document run (where the code needs work)

Scaled the worker: `kb.promote.silver`/`kb.promote.gold` partitions 1 → 3, worker
replicas 1 → 3 (removed the worker's `container_name` so `--scale` works; the
producer keys promote messages by `data_id` so docs hash across partitions).
Kafka rebalanced cleanly — each of the 3 workers owns one silver + one gold
partition. Then ingested **36 KB docs that chunk into 70 chunks each**.

**Result: scaling the workers barely moved throughput.** The 3 workers sat at
~0.5 % CPU (idle-waiting); the bottleneck is the **shared LLM tier**. Under 3
concurrent workers, `kb_llm_generate_ms_avg` ballooned from ~2 s to **29–34 s per
call** (they all funnel into one ollama, which serialises on the single GPU), and
the whole batch crawled at **~0.2 `/generate`/s**. 6×70-chunk docs make *hundreds*
of LLM calls — silver alone hadn't finished after 5 min. **Ingestion is
LLM-bound, and the LLM is a single shared queue, so adding workers is wasted until
the LLM tier itself scales.**

**Code optimisation targets (ranked):**

1. **Gold processes chunks sequentially** — `for chunk in chunks: await
   _process_chunk(...)` (`backend/services/kb-ingestion-worker/gold.py:1065`). 70 chunks ×
   ~per-chunk LLM+embed run strictly one-after-another. Process chunks with a
   **bounded `asyncio.gather`** (e.g. 4–8 in flight) so LLM/embed waits overlap and
   actually use `OLLAMA_NUM_PARALLEL`. (Care: cross-chunk graph build + canonical
   dedup + conflict batch need ordering-safe handling.)
2. **Per-chunk LLM calls** — gold calls the LLM per chunk for canonicalize +
   conflict-detect (+ merge-verify), and silver extracts per chunk. **Batch N
   chunks into one `/generate`** (like the embed dynamic-batcher) to cut call count
   ~N×.
3. **`model-service /generate` has no batching/queue** (unlike the embed path).
   Add dynamic batching / a small admission queue so concurrent worker calls
   coalesce instead of each blocking ~34 s.
4. **Scale the LLM tier, not the workers** — the shared ollama is the ceiling;
   more workers only help once there's a GPU pool / multiple LLM replicas / higher
   `OLLAMA_NUM_PARALLEL` backed by VRAM. Worker count should track LLM capacity.
5. **Reduce per-chunk LLM need** — gate canonicalize/conflict LLM calls (only when
   entities actually overlap/conflict) or do a single doc-level pass instead of one
   per chunk.

The scaling mechanism is in place (partitions + `--scale`); the win is now a
**pipeline-code + LLM-tier** problem, not an orchestration one.

#### Pipeline optimisation implemented + the single-GPU ceiling

**Code (done):** gold's chunk loop (`gold.py`) was strictly sequential
(`for chunk: await _process_chunk`). Made it **bounded-concurrent** via
`GOLD_CHUNK_CONCURRENCY` (default 1 = exact legacy behaviour; >1 overlaps the
per-chunk LLM+embed waits), mirroring silver's existing `_summarize_chunks`
gather. Also exposed `SILVER_CONCURRENCY` (docs-in-silver, default 1) as a compose
passthrough. Both are opt-in so default correctness/dedup is unchanged. The entity
registry uses deterministic UUIDs + upserts, so concurrency can't create duplicate
canonicals — worst case is slightly weaker same-batch dedup.

**Measured on this box (GOLD_CHUNK_CONCURRENCY=4, SILVER_CONCURRENCY=3): ~no
throughput gain.** Workers stayed ~0.5 % CPU, `/generate` held **~0.16–0.30
calls/s** with `ms_avg` 21–34 s — *identical* to the sequential run. Reason: the
**single M3 GPU is saturated** — issuing more concurrent LLM calls just splits the
same GPU, so each call slows proportionally and total tokens/s is flat. **The
optimisation is production-correct** (it wins when the GPU has spare capacity or
behind a GPU pool) **but on one GPU the LLM tier is a hard ceiling.** The only
lever that helps *here* is a **smaller/faster model** (measured earlier: 0.5b
~170 tok/s vs 1.5b ~94 vs 3b ~55) or **fewer LLM calls per chunk**.

#### Huge-file limit test (one 222 KB / 500-section document)

Bronze **accepted** it (limit is `MAX_FILE_SIZE_MB=200`, so ~900× headroom).
Docling chunks ~**1 chunk per section** (the 36 KB/70-section doc → exactly 70
chunks), so this file → ~500 chunks → ~500+ LLM calls. At the GPU-bound
~0.2–0.3 calls/s that is **~30–45 min for a single file**, and it queues behind any
other docs on its partition. **The limit is not size or a chunk cap — it's LLM/GPU
time.** Practical guidance: (1) a huge doc monopolises its partition for a long
time — shard very large docs or give them a dedicated topic/worker; (2) throughput
scales with **GPU capacity**, not worker/partition count; (3) cap chunks-per-doc or
batch per-chunk extraction to bound worst-case ingestion time.

---

## 7. Cost envelope (rough, monthly, cloud)

| Scope | Rough infra | Comment |
|---|---|---|
| **Retrieval + CRUD @ 10k CCU** | ~2 GPU (embed) + ~12–16 general pods + managed PG/Redis/Qdrant | **modest** — caching does the heavy lifting |
| **+ RAG generation @ 10k CCU** | a **vLLM GPU fleet** (dozens of A100/H100) | order-of-magnitude bigger; keep it a separate, independently-scaled service behind the queue |

**Bottom line:** 10k CCU on **retrieval** is a *caching + one GPU + horizontal
stateless scale + connection pooling* problem — very tractable. The moment
"answer generation" is in scope, that becomes its own capacity project (vLLM),
which is why the architecture keeps generation behind Kafka and off the
synchronous retrieval path.
