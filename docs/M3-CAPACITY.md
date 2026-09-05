# Running the KB stack on an Apple-Silicon laptop (M3, 18 GB)

How to run the full Knowledge-Base stack on a MacBook (M3 Pro, 11 cores, 18 GB)
for high-concurrent-user (CCU) development without swapping / lagging the machine,
and the language split (Go vs Python) that keeps the CCU-hot paths fast.

> **Note (30-08-2026):** `kb-gateway` and `kb-compute` were removed — rate limiting is now
> Kong's `rate-limiting` plugin on `kb-backend` (`deploy/docker/kong/kong-setup.sh` §10) and
> RRF fusion runs in-process in `kb_backend` (`rrf_merge`). References below to those
> services are historical measurements, kept as-is.

---

## 1. Machine baseline

| | |
|---|---|
| Host | Apple M3 Pro · 11 cores (5 performance + 6 efficiency) · **18 GB** unified memory · Metal GPU |
| Docker (OrbStack) VM | shares the host — was capped at **~9.4 GB** |
| ollama | runs **natively on the host** (Metal GPU), NOT in the VM — its model competes for the same 18 GB |

## 2. Why it lagged (measured)

The default stack oversubscribes 18 GB:

```
9.4 GB  OrbStack VM (21 containers, ~5.5 GB steady + spikes)
+ 6.0 GB  ollama 7B model on the host  (qwen2.5vl:7b — a VISION model, worst choice for text)
+ 4.0 GB  macOS + IDE
= 19.4 GB > 18 GB  →  swap  →  "docker ps hangs", UI lag
```

Two structural wastes in the VM:
- **~1.9 GB of observability** not needed for KB dev: elasticsearch (731 MB), clickhouse (412 MB), langfuse ×2 (511 MB), kibana (158 MB), filebeat (82 MB), jaeger.
- **Over-provisioned JVM stores**: base config asks Neo4j for `heap 4g + pagecache 3g` and a 10 GB limit; Mongo's WiredTiger defaults to 50 % of RAM.

## 3. The fix — three levers (~8 GB reclaimed)

| Lever | Reclaims | How |
|---|---|---|
| LLM 7B → **3B** (`qwen2.5:3b`, 1.9 GB) | ~4 GB host | `REMOTE_LLM_MODEL=qwen2.5:3b` (measured: entity-extraction call 4.1 s vs 60 s+ timeout on the 7B vision model) |
| **Drop observability** | ~1.9 GB VM | don't select those services (they're profile/opt-in) |
| **Cap heaps** (Neo4j/Mongo/Kafka/Keycloak) | ~1 GB VM | `resources.limits` + the `*_HEAP`/`*_JAVA_OPTS` env in each `deploy/k8s/database/*.yaml` |

### Bring up the lite stack

```bash
# 1. Stores. Lower resources.limits in each manifest for a laptop cluster
#    (k3s / kind / Docker Desktop) — the committed values target a real node.
kubectl apply -f deploy/k8s/database/

# 2. App tier
kubectl apply -f deploy/k8s/service/

# 3. Native ollama with a small model, 2 parallel slots
OLLAMA_NUM_PARALLEL=2 ollama serve   # (or the app; then: ollama pull qwen2.5:3b)
```

### Budget after the fix

```
18 GB host
├─ macOS + IDE + Claude   ~4.5 GB
├─ ollama (3B + KV cache) ~3.0 GB   (Metal GPU)
└─ OrbStack VM (cap 8 GB)
   ├─ KB app  ~1.2 GB   (kb-backend .25 · worker .45 · model-service .25)
   ├─ infra   ~2.2 GB   (mongo .5 · neo4j .75 · kafka .5 · keycloak .5 · kong .25 · minio .25 · pg/qdrant/redis ~.5)
   └─ headroom ~4 GB    (embed/ingest spikes)
```
→ ~15.5 GB committed, ~2.5 GB free → **no swap, no lag.**

> Optionally lower the OrbStack VM RAM cap to 8 GB (OrbStack → Settings → Resources) so the host always keeps room for ollama.

## 4. High-CCU: where the bottleneck actually is

**The CCU ceiling is the LLM, not the web tier.** Classify every request path:

| Path | Bottleneck | CCU on M3 |
|---|---|---|
| **Retrieval** (interactive) | embedding (fast, batchable) + *optional* canonicalize-LLM | **High** — bypass the LLM by passing front-layer `entities`; e5-small embed ~10–50 ms/query |
| **CRUD / list / documents** | Postgres + Qdrant I/O | **High** — pure I/O, async scales to hundreds |
| **Ingestion bronze** | MinIO + PG + Kafka I/O | **High** — async, decoupled via Kafka queue |
| **Ingestion silver/gold** | **LLM entity-extraction** (many calls/doc) | **Low** — serialized on one model; that's fine, it's a background queue |
| **RAG answer generation** | **LLM** | **Low** on one laptop GPU |

**Rules for CCU on a laptop:**
1. **Decouple AI from user-facing.** Interactive users hit retrieval + CRUD (fast). Ingestion + generation go through the **Kafka queue** and never block a user.
2. **The local LLM is a single queue** (one GPU). Use `OLLAMA_NUM_PARALLEL=2` + a 3B model for dev/demo (~3–5 req/s). **Real CCU load testing of generation must target a remote/cloud LLM (vLLM cluster / API)** — a laptop is not the place to measure LLM throughput.
3. **Test CCU the right way** (see the k6 script, §6): drive **retrieval + CRUD with the LLM bypassed/mocked**. That measures what the M3 can actually serve concurrently; don't push load *through* the local LLM.

## 5. Language split — Go vs Python (no ambiguity)

**Python is not the bottleneck.** In order, the costs are: ① LLM inference (language-independent — ollama is already Go/C++), ② JVM infra (Neo4j/Kafka), ③ DB I/O. FastAPI's async web tier is I/O-bound, so a Go rewrite of it changes CCU very little — but Go **does** win for the two places the user called out: the **high-fan-out edge** and the **CPU-bound deterministic kernels**.

### The one rule (so it's never ambiguous)

> **A module is Go by default. It is Python ONLY if it (a) imports a Python-only ML library (torch / sentence-transformers / transformers), or (b) builds or parses LLM prompts/outputs, where fast iteration on prompt logic matters.**
> Everything deterministic, CPU-bound, or high-concurrency-I/O → **Go**.

### Component-by-component

| Component | Language | Why |
|---|---|---|
| **Edge / BFF / gateway** — auth-header handling, request fan-out to backends, SSE streaming, rate-limit, per-tenant routing | **Go** | goroutines = thousands of cheap concurrent conns, ~10× less RAM/req than Python workers; this is the CCU front door (complements Kong) |
| **Compute kernels** — RRF fusion, BM25 scoring, reciprocal-rank merge, chunk splitting, entity canonicalization + de-dup, graph-edge building | **Go** | deterministic, CPU-bound, no ML deps; Go is 10–50× faster and GC-light. Ship as one `kb-compute` gRPC service |
| **Vector math glue** (assemble Qdrant/Neo4j queries, pagination, RLS filters) | **Go** | pure I/O + logic |
| **model-service** — embed, rerank, tokenizer, local LLM proxy | **Python** | sentence-transformers / PyTorch are Python-only |
| **LLM orchestration in ingestion** — prompt building for entity extraction, parsing/validating LLM JSON, retry policy | **Python** | rule (b): prompt logic iterates constantly; keep it in Python next to the models |
| **Data plane** — Postgres, Qdrant, Neo4j, Kafka, MinIO | *(external)* | accessed by whichever layer owns the step |

### Boundary interface (how Go ↔ Python talk)

- **Transport:** gRPC (typed, fast) or HTTP+JSON. One `.proto` per boundary, checked in under `backend/libs/proto/`.
- **Two seams only** (keep it small):
  1. `kb-compute` (Go) ⇄ `model-service` (Python): Go asks Python for vectors/rerank scores; Go does the fusion/ranking.
  2. `kb-ingestion-worker` (Python LLM steps) ⇄ `kb-compute` (Go): Python hands raw entities/chunks to Go for canonicalization + graph-build.
- **No shared DB writes across languages for the same table** — each step's owner writes; the other reads via its API. This is the anti-ambiguity guard.

### Proposed layout (incremental — nothing forces a big-bang rewrite)

```
backend/services/
  kb-backend/         # Python — keep (business/CRUD/ORM); thin routing may move to the Go BFF
  kb-ingestion-worker/# Python — LLM steps only (prompt build + parse); CPU steps call kb-compute
  model-service/      # Python — embed/rerank/LLM  (unchanged)
  (kb-gateway/ and kb-compute/ — removed 30-08-2026, see note at top)
backend/libs/
  proto/              # .proto contracts for the Go↔Python seams
```

### Migration order (each step ships independently, measurable)

1. **kb-compute (Go)** — port the pure functions first (RRF fusion, BM25, entity canonicalization from `pipeline/light_rag` + `kb-ingestion-worker/utils/entity_registry.py`). Biggest CPU win, zero ML risk, easy to A/B against the Python versions (they're already unit-tested).
2. **kb-gateway (Go)** — move fan-out/SSE/auth-header handling out of kb-backend; kb-backend keeps business endpoints behind it.
3. Leave model-service + LLM-orchestration in Python permanently.

Rough win: kernels 10–50× faster (µs, not the current bottleneck — do it for CPU headroom under load, not latency), and the Go edge sustains far higher CCU per GB than Python workers.
