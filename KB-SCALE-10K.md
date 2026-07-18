# KB @ 10,000 CCU — engineering brief

How to make the Knowledge-Base **retrieval** tier sustain 10,000 concurrent
users. This is the problem statement + the sizing math + the concrete changes to
implement. Grounded in measured per-request costs (this stack, 2026-07).

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
| **Edge — kb-gateway (Go)** | 20k+ conns/instance | **2–3** | stateless + HPA | already scaffolded; Go = cheap concurrency |
| **Kong** | ~10k RPS/node | **2** | stateless | JWT/JWKS verify; keep |
| **kb-backend (Py async)** | ~300–500 RPS/replica (I/O-bound) | **6–8** | stateless + HPA | 2 vCPU / 512 MB each |
| **model-service (embed/rerank)** | GPU ~800/s batched · CPU ~1.7/s | **2 GPU** (or heavy cache) | stateless + HPA | **the sizing crux**; dynamic batching ON |
| **Qdrant** | ~1–3k QPS/node | **3 nodes** (replicated + sharded) | stateful | HNSW; replicate for read QPS |
| **Postgres** | writes 1 primary | **1 primary + 2 read replicas** | stateful | **PgBouncer** (transaction pooling) in front; RLS reads hit replicas |
| **Neo4j** | reads scale w/ replicas | **1 leader + 1–2 read replicas** | stateful | or make graph-expand optional (§2) |
| **Redis** | 100k+ ops/s | **1 primary + 1 replica** | stateful | query + embedding cache |
| **Kafka** | — | **3 brokers** | stateful | ingestion only, not user CCU |
| **kb-compute (Go)** | ~50k RPS (RRF is µs) | **2** | stateless | RRF/BM25/dedup off Python |
| **kb-ingestion-worker (Py)** | N docs/min | **scale by backlog** | queue consumer | async; not user CCU |

**Connection math (the classic 10k-CCU killer):** never let 10k users translate
to 10k DB connections. kb-backend async pool (~20 conns/replica) × 8 replicas =
160 → **PgBouncer** multiplexes to ~50 real Postgres connections. Qdrant/Neo4j
likewise pooled per replica.

---

## 4. Architecture changes to implement (checklist)

**Already done in this repo:**
- [x] `kb-gateway` (Go) — high-CCU edge/BFF (scaffold).
- [x] `kb-compute` (Go) — RRF/BM25/dedup kernels off the Python hot path (scaffold).
- [x] Kafka-decoupled ingestion; query cache; stateless kb-backend.

**To build (in priority order):**
1. [ ] **Embedding cache** in model-service (hash→vector, Redis) + **dynamic
   batching** of embed requests.
2. [ ] **Result-cache hardening**: Redis, tenant-scoped key, TTL, invalidate on
   `kb.promote.gold`.
3. [ ] **PgBouncer** (transaction pooling) in front of Postgres; point RLS reads
   at **read replicas**.
4. [ ] **Qdrant replication** (read QPS) + **Neo4j read replica** (or degrade).
5. [ ] **Graceful degradation** flags: per-request budget; skip graph_expand /
   rerank when p95 breaches SLO; circuit-breaker on model-service/Neo4j.
6. [ ] **Per-tenant rate limiting** at kb-gateway/Kong (protect the shared pool).
7. [ ] **HPA** on kb-gateway / kb-backend / model-service (target 60 % CPU or a
   custom RPS metric); GPU node pool for model-service.
8. [ ] **Observability SLOs**: p95 retrieval < 500 ms (cached) / < 1.5 s (cold),
   error rate < 0.1 %, cache hit-rate dashboard, per-tier saturation.

---

## 5. Deployment shape (Kubernetes)

- One **GPU node pool** (model-service) + one **general pool** (everything else).
- HPA on the 3 stateless tiers; PodDisruptionBudgets; readiness = the existing
  `/health`.
- Qdrant/Postgres/Neo4j/Kafka/Redis as StatefulSets or managed services
  (managed Postgres + managed Redis strongly recommended — offload the hard
  stateful ops).
- Ingress → Kong (authN) → kb-gateway (Go BFF) → kb-backend (Py) → data plane.

---

## 6. Load test to PROVE 10k CCU (don't guess)

Run from a **k8s staging cluster or k6 Cloud**, not a laptop.

- Extend `scripts/loadtest/ccu-test.js`: ramp VUs to model 10k CCU **with
  think-time** (`sleep(15±5s)` per iteration → ~667 RPS), realistic mix (60/40),
  and a **cold:warm query ratio** (e.g. 30 % unique queries) so you actually
  exercise embed, not just the cache.
- Stages: 1k → 3k → 6k → 10k VUs, hold 10 min each.
- **Pass criteria:** p95 within SLO at 10k VUs, error < 0.1 %, no tier > 80 %
  saturation, cache hit ≥ target. Find the knee, then size replicas to sit at
  ~60 % of it.

```bash
# 10k CCU model (think-time built into the script; run distributed)
VUS=10000 DURATION=10m THINK=15 k6 run scripts/loadtest/ccu-test.js
```

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
