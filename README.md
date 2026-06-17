# GTEL Data Agent — AeroFlow AI OS

Multi-tenant Enterprise AI OS for RAG orchestration: a React Knowledge Base UI on top of a
workflow runtime, flow builder, knowledge-base backend, auth/gateway services, storage layers,
and observability tooling.

The active focus of this repository is the **Knowledge Operations Center** frontend under
[`src/components/knowledge/`](./src/components/knowledge), backed by the `kb-backend` service and
the `kb-ingestion-worker`.

## Repository Layout

| Path | Contents |
|---|---|
| [`src/`](./src) | React 19 + TypeScript + Vite frontend (Knowledge Base UI). `src/lib/mockApi.ts` is the FastAPI client |
| [`services/`](./services) | Backend microservices: `kb_backend`, `auth-api`, `flow-builder`, `workflow-runtime`, `minio-service`, `release-worker`, `audit-bridge`, `audit-consumer`, `jwks-refresher` |
| [`infra/`](./infra) | Docker Compose stack, service Dockerfiles, and infra services (`docling`, `model-service`, `kb-ingestion-worker`, `Postgres`, `Neo4j`, `Qdrant`, `kafka`, `kong`) |
| [`basemodel/`](./basemodel) | Shared data models and database connectors |
| [`alembic/`](./alembic) | Database migrations |
| [`docs/`](./docs) | Architecture and subsystem docs |
| [`archive/`](./archive) | Legacy Docker artifacts — do not use |

---

## Running the KB-UI (backend + frontend)

End-to-end startup. The frontend has **no mock server** — it talks to the real `kb-backend`, so the
backend stack must be up first.

### 1. One-time setup

```powershell
npm install                                                       # frontend deps
Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing     # gitignored env (see Local-only files)
```

### 2. Start the backend stack (Docker)

Brings up Postgres, Qdrant, Neo4j, MongoDB, MinIO, Kafka, model-service, `kb-backend`, and the
`kb-ingestion-worker`. Postgres tables and Qdrant collections are created automatically on first start.

```powershell
docker compose -f infra/docker-compose.kb-ui-testing.yml --env-file infra/.env.kb-testing up -d --build
```

> First start is slower: `model-service` downloads its embed/rerank weights into
> `infra/model-service/models/` (~2.3 GB, persisted for next time) — see
> [Local-only files](#local-only-files-not-baked-into-images-or-git).

### 3. Verify the backend is healthy

```powershell
docker compose -f infra/docker-compose.kb-ui-testing.yml ps     # every service "healthy"
curl http://localhost:8050/health                               # -> {"status":"ok"}
```

### 4. Start the frontend

```powershell
npm run dev      # Vite dev server on http://localhost:3000
```

Open http://localhost:3000 — the UI calls `kb-backend` at `http://localhost:8050` (override with
`VITE_API_URL`).

### Shutdown

```powershell
docker compose -f infra/docker-compose.kb-ui-testing.yml down       # stop, keep data (volumes)
docker compose -f infra/docker-compose.kb-ui-testing.yml down -v    # also wipe DB volumes
```

---

## LightRAG Retrieval API

The `kb-backend` exposes the LightRAG retrieval pipeline over HTTP. Use it to fetch the top-ranked
Gold-layer chunks for a query and feed them to your own LLM for RAG.

**Base URL:** `http://localhost:8050` (the `kb-backend` service)

| Method & path | Purpose |
|---|---|
| `POST /api/pipeline/light-rag/retrieve` | Run the full pipeline, return ranked chunks (JSON) |
| `POST /api/pipeline/light-rag/retrieve/stream` | Same, streamed as SSE — one event per step (progress UIs) |
| `GET  /api/pipeline/light-rag/health` | `{"status":"ok"}` when the pipeline is wired |

### Request

Identity comes from request headers (used for multi-tenant scoping):

```
Content-Type: application/json
X-Role: AI_ENGINEER
X-Tenant-Id: 00000000-0000-0000-0000-000000000001
X-User-Id: dev-user
```

Body fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string | — | **required** — the natural-language question |
| `knowledge_base` | string | `""` (all) | restrict retrieval to one knowledge base |
| `collection` | string | `knowledge` | chunk collection to search |
| `tenant_id` | string | from `X-Tenant-Id` | overrides the header |
| `entities` | string[] | `[]` | front-layer entities — skips the internal canonicalize LLM and uses these as the pass-1 filter + graph seeds |
| `intent` | string | `""` | front-layer query intent label |
| `entity_types` | object | `{}` | `{name: type}` e.g. `{"Việt Minh":"organization"}` — per-entity typed search |

```bash
curl -X POST http://localhost:8050/api/pipeline/light-rag/retrieve \
  -H 'Content-Type: application/json' \
  -H 'X-Role: AI_ENGINEER' \
  -H 'X-Tenant-Id: 00000000-0000-0000-0000-000000000001' \
  -H 'X-User-Id: dev-user' \
  -d '{"query":"Điều kiện thành lập thư viện cơ sở?","knowledge_base":""}'
```

### Response

```jsonc
{
  "source": "pipeline",            // "pipeline" = full run; "cache" = served from the query cache
  "canonical": {                   // query understanding from the canonicalize step
    "intent": "general",
    "entities": [],
    "relationships": []
  },
  "results": [                     // ranked chunks — THIS is what you feed the LLM
    {
      "chunk_rank": 1,             // 1 = best match
      "chunk_score": 0.534,        // 0–1 relevance (cross-encoder, sigmoid-squashed)
      "chunk_context": "…full chunk text from the active Postgres version…",
      "table": [],                 // [{table_name: description}, …] when the chunk carries a table
      "source": {
        "block_id":    "86e86eb6-…",  // chunk id (Qdrant point ↔ Postgres KBTextBlock)
        "block_index": 0,             // position within the document
        "doc_id":      "98fff8e8-…",  // owning document (KBData)
        "doc_name":    "59984",
        "source_type": "doc",         // doc | web | image | video
        "tier":        "gold"
      }
    }
    // … up to RERANK_TOP_N results (default 5)
  ],
  "timings": { "embed_query_ms": 12.0, "rerank_ms": 80.1, "total_ms": 420.0 }
}
```

### Using it for RAG

1. `POST` the user's question to `/retrieve`.
2. Build the LLM context from `results` in `chunk_rank` order — concatenate each `chunk_context`
   (plus any `table`); optionally drop low-`chunk_score` chunks.
3. Cite from each `source` (`doc_name`, `doc_id`, `block_id`, `tier`).
4. Send `{context + user question}` to your generation model.

`results` is `[]` when nothing relevant is found (e.g. an empty knowledge base) — treat that as
"no answer in the knowledge base." Repeated near-identical queries return `"source":"cache"` with the
same shape (no LLM re-run).

For a progress UI, call `/retrieve/stream`: it returns `text/event-stream` with one
`data: {"step":"…","ms":…}` line per stage (`embed_query`, `cache_lookup`, `canonicalize`,
`qdrant_pass1`, `graph_expand`, `qdrant_pass2`, `rrf_merge`, `rerank`, `hydrate`, …) and a final
`data: {"step":"done", …same fields as the JSON response…}`.

---

## Frontend Development

The Knowledge Base UI is a Vite dev server. Its data layer (`src/lib/mockApi.ts`) is a thin fetch
wrapper around the **real KB FastAPI backend** — there is no standalone mock server on this branch.
Bring up the backend stack first (see [Full Stack — Docker](#full-stack--docker)), then:

```powershell
npm install
npm run dev 
```

The dev server talks to the backend via:

- `VITE_API_URL` — base URL for KB API calls (default `http://localhost:8050`, the `kb-backend` service)
- A Vite proxy that forwards `/api/release` and `/api/auth` to the Kong gateway at `http://host.docker.internal:8000`

Set `VITE_API_URL` in your environment if the backend is reachable on a different host/port.

Other scripts:

```powershell
npm run lint          # tsc --noEmit (type check)
npm run build         # production build
npm run test          # vitest run
```

| Service | URL |
|---|---|
| Frontend (Vite dev) | http://localhost:3000 |
| KB backend API (`VITE_API_URL`) | http://localhost:8050 |

---

## Full Stack — Docker

The Docker stack lives under [`infra/`](./infra). Use these files as the source of truth:

- [`infra/docker-compose.yml`](./infra/docker-compose.yml): aggregate entrypoint with every service group
- [`infra/docker-compose.storage.yml`](./infra/docker-compose.storage.yml): storage layer
- [`infra/docker-compose.security.yml`](./infra/docker-compose.security.yml): auth, gateway, and secret-management layer
- [`infra/docker-compose.messaging.yml`](./infra/docker-compose.messaging.yml): messaging layer
- [`infra/docker-compose.observability.yml`](./infra/docker-compose.observability.yml): observability and AI-support layer
- [`infra/docker-compose.app.yml`](./infra/docker-compose.app.yml): application and frontend layer
- [`infra/docker-compose.local.yml`](./infra/docker-compose.local.yml): local developer override (adds `migrate` + `seed`)
- [`infra/docker-compose.dev.yml`](./infra/docker-compose.dev.yml): shared dev server override (adds `migrate` + `seed`)
- [`infra/docker-compose.test.yml`](./infra/docker-compose.test.yml): test-only PostgreSQL fixture
- [`infra/docker-compose.kb-ui-testing.yml`](./infra/docker-compose.kb-ui-testing.yml): isolated KB-UI integration stack

This repository still contains older Docker artifacts under [`archive/docker-legacy/`](./archive/docker-legacy)
and [`infra/old/`](./infra/old). Do not use them.

### Prerequisites

- Docker Desktop with Docker Compose v2
- A local `.env` created from [`.env.example`](./.env.example)

Bootstrap once:

```powershell
Copy-Item .env.example .env
```

Then fill in the required values in `.env`, especially:

- `POSTGRES_PASSWORD`
- `KEYCLOAK_ADMIN_PASSWORD`
- `MINIO_ROOT_PASSWORD`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`
- `KAFKA_ADMIN_PASSWORD`
- `AUDIT_BRIDGE_KAFKA_PASSWORD`
- `AUDIT_CONSUMER_KAFKA_PASSWORD`
- `RELEASE_WORKER_KAFKA_PASSWORD`

### Local-only files (not baked into images or git)

The kb-backend image ([`infra/Dockerfile.kb`](./infra/Dockerfile.kb)) bakes in **only application code**
— `basemodel/` and `services/`. It contains no secrets, datasets, model weights, or DB seed data;
those are supplied at runtime via environment variables, host bind-mounts, and Docker volumes.

A few of those inputs are **gitignored**, so a fresh clone will not have them and must recreate them
before bringing the KB-UI testing stack up:

| Local-only file | Used by | What to do |
|---|---|---|
| `infra/.env.kb-testing` | kb-ui-testing compose interpolation — MinIO credentials and the optional `VLLM_BASE_URL` / `VLLM_API_KEY` LLM tunnel | `Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing`, then edit. The example defaults work for basic local testing. |
| `model/ollama/weights/model.gguf` (~5.7 GB) | the optional GPU `llama` service (mounted `../model/ollama/weights:/models`) | Download a GGUF model into that path. **Only** needed if you run the local `llama` service — the default KB stack reaches the LLM via `VLLM_BASE_URL` and does not require it. |

Auto-managed (downloads itself — no manual file placement):

- **`infra/model-service/models/`** — where the embedding + reranker weights are saved. They are
  **not** baked into the model-service image; on the **first** `model-service` start they download
  here (~2.3 GB for the configured `EMBED_MODEL` + `RERANK_MODEL`) and persist across recreates, so
  later starts are instant. This keeps the image ~2.5 GB smaller. Consequences:
  - the first bring-up needs network and the model-service healthcheck has a 300 s `start_period`
    (kb-backend waits for it), so first start is slower — this is expected, not an error;
  - to pre-seed offline (skip the first-run download), populate the folder from another machine's
    `infra/model-service/models/`, or run:
    `docker compose -f infra/docker-compose.kb-ui-testing.yml run --rm model-service python -c "import models, config as c; models.load(c.EMBED_MODEL, c.RERANK_MODEL)"`
- DB content lives in Docker **named volumes** (persist across image rebuilds) and is seeded by the
  tracked [`infra/Postgres/init.sql`](./infra/Postgres/init.sql) + [`infra/Postgres/seed.sql`](./infra/Postgres/seed.sql).

### KB-UI testing stack

Isolated stack for exercising the KB backend + ingestion worker against the UI:

```powershell
docker compose -f infra/docker-compose.kb-ui-testing.yml --env-file infra/.env.kb-testing up -d --build
```

The kb-backend health endpoint confirms it is live:

```powershell
curl http://localhost:8050/health   # -> {"status":"ok"}
```

Switch the model-service backend (LOCAL CPU vs. remote Colab GPU) with
[`infra/model-backend.sh`](./infra/model-backend.sh) — no image rebuild required.

### Official Docker Commands

Run all commands from the repository root.

#### Local machine

Start or refresh the full local stack:

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --build
```

Stop the local stack and keep data:

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml down
```

Stop the local stack and remove volumes:

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml down -v
```

#### Shared dev server

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d --build
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml down
```

#### Compose validation

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml config
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml config
```

#### Test fixture

Start the isolated PostgreSQL fixture used by connector and Alembic tests:

```powershell
docker compose -f infra/docker-compose.test.yml up -d
docker compose -f infra/docker-compose.test.yml down -v
```

### Runtime Shape

The base stack includes these service groups:

- **Storage:** PostgreSQL, MongoDB, Redis, MinIO
- **Search and graph:** Qdrant, Neo4j
- **Security and gateway:** Keycloak, Kong, OpenBao
- **Messaging:** Kafka
- **Observability and AI tooling:** LiteLLM, Langfuse, Jaeger, Elasticsearch
- **Application services:** workflow-runtime, flow-builder, kb-backend, kb-ingestion-worker, model-service, auth-api, minio-service, release-worker, audit-bridge, audit-consumer, jwks-refresher
- **Frontend:** Vite server on `5173`

The local and shared-dev overrides both add `migrate` and `seed` jobs.

Separate intent-specific Docker paths also exist:

- Test-only: [`infra/docker-compose.test.yml`](./infra/docker-compose.test.yml)
- KB-UI integration: [`infra/docker-compose.kb-ui-testing.yml`](./infra/docker-compose.kb-ui-testing.yml)
- Standalone model experiment: [`model/ollama/docker/docker-compose.yml`](./model/ollama/docker/docker-compose.yml)
- Legacy archive: [`archive/docker-legacy/`](./archive/docker-legacy)

### Common URLs (Docker stack)

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Kong proxy | http://localhost:8000 |
| Kong admin | http://localhost:8900 |
| workflow-runtime | http://localhost:8001 |
| flow-builder | http://localhost:8002 |
| kb-backend | http://localhost:8050 |
| release-worker | http://localhost:8100 |
| auth-api | http://localhost:8200 |
| minio-service | http://localhost:8400 |
| model-service | http://localhost:8600 |
| Keycloak | http://localhost:8080 |
| Langfuse | http://localhost:3001 |
| LiteLLM | http://localhost:4000 |
| Jaeger | http://localhost:16686 |
| MinIO console | http://localhost:9001 |
| Qdrant | http://localhost:6333 |

### Notes

- `docker compose config` requires a real `.env` because several services reference `env_file: ../../.env`.
- Container names are prefixed `dataagent-*` and share the `data-agent-network` Docker network.
- The Keycloak realm is `aeroflow`; the frontend authenticates against it via the `VITE_KEYCLOAK_*` variables.
- Neo4j is exposed only on the internal network (ports `7474`/`7687`) and is not published to the host by default.
