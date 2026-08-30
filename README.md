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
| [`src/`](./src) | React 19 + TypeScript + Vite frontend (Knowledge Base UI). `src/lib/apiClient.ts` is the FastAPI client (bearer-only — Kong injects identity) |
| [`services/`](./services) | Backend microservices: `kb_backend`, `kb-ingestion-worker`, `auth-api`, `flow-builder`, `workflow-runtime`, `orchestrator`, `model-service`, `minio-service`, `sandbox-executor`, `schema-crawler-worker`, `release-worker`, `audit-bridge`, `audit-consumer`, `memory-consumer`, `jwks-refresher` |
| [`libs/`](./libs) | Shared cross-service layer: `libs/basemodel` (domain models/ORM), `libs/database_connector` (infra clients), `libs/shared` (logger, auth, observability). Innermost architecture layer — enforced by `.importlinter` |
| [`infra/`](./infra) | Docker Compose stack + service infra (`Postgres`, `Neo4j`, `Qdrant`, `MinIO`, `kafka`, `kong`, `keycloak`, observability) |
| [`alembic/`](./alembic) | Database migrations |
| [`RUNBOOK.md`](./RUNBOOK.md) · [`SECURITY.md`](./SECURITY.md) | Deploy/operate runbook · security posture & release hardening |

---

## Docker stacks at a glance

There is no mock server — the frontend talks to the real `kb-backend`, which is **fail-closed**:
every API request must arrive through **Kong** carrying a verified Keycloak identity (`X-Kong-Verified`),
or it is rejected with `401`. So a usable stack needs **Keycloak** (login) + **Kong** (gateway) in
front of `kb-backend`. Pick the stack that matches your task:

| Stack | File / helper | What it is | When to use |
|---|---|---|---|
| **kb_e2e** | `infra/docker-compose.kb_e2e.yml` | KB data plane + ingestion worker + Keycloak + DB-less Kong; richer LLM options (vLLM / local `llama`) and multilingual models | **The standard way to bring up the stack** — local dev with the Vite UI (`npm run dev`) and the ingestion/RAG pipeline |
| **Full aggregate** | `infra/docker-compose.yml` (+ `local`/`dev` override) | Every service group (storage, security, messaging, observability, all app services) | Running the whole platform, not just KB |
| **Test fixture** | `infra/docker-compose.test.yml` | Isolated PostgreSQL only | Connector / Alembic tests |

> `kb-backend` is also published on `:8050` — but hitting it directly bypasses Kong, so it returns
> `401` by design (handy to confirm fail-closed behaviour).

---

## Running the KB-UI — kb_e2e (full local dev)

End-to-end local development with the Vite dev server, the ingestion pipeline, and the auth chain.

### 1. One-time setup

```powershell
npm install                                                       # frontend deps
Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing     # gitignored env (see Local-only files)
```

### 2. Start the stack (Docker)

Brings up Postgres, Qdrant, Neo4j, MongoDB, MinIO, Kafka, model-service, `kb-backend`,
`kb-ingestion-worker`, **Keycloak** (`:8080`), and a DB-less **Kong** gateway (`:8000`). Postgres
tables (incl. a dedicated `keycloak` DB), Qdrant collections, and the Kong route/JWT plugin are all
created automatically on first start.

```powershell
docker compose -f infra/docker-compose.kb_e2e.yml --env-file infra/.env.kb-testing up -d --build
```

> First start is slower: `model-service` downloads its embed/rerank weights into
> `infra/model-service/models/` (~2.3 GB, persisted for next time) — see
> [Local-only files](#local-only-files-not-baked-into-images-or-git).

### 3. Verify the stack is healthy

```powershell
docker compose -f infra/docker-compose.kb_e2e.yml ps     # every service "healthy"
curl http://localhost:8050/health                               # -> {"status":"ok"} (direct; bypasses Kong)
```

### 4. Start the frontend

The UI logs in through Keycloak and sends API traffic through Kong (`:8000`), so point it there:

```powershell
$env:VITE_KEYCLOAK_URL    = "http://localhost:8080"
$env:VITE_KEYCLOAK_REALM  = "aeroflow"
$env:VITE_KEYCLOAK_CLIENT = "aeroflow-frontend"
$env:VITE_API_URL         = "http://localhost:8000"   # Kong proxy — NOT :8050
npm run dev                                            # Vite dev server on http://localhost:3000
```

Open http://localhost:3000, log in as **`helios-admin` / `H3lios@Admin01!`**, and the UI talks to
`kb-backend` through Kong.

### Shutdown

```powershell
docker compose -f infra/docker-compose.kb_e2e.yml down       # stop, keep data (volumes)
docker compose -f infra/docker-compose.kb_e2e.yml down -v    # also wipe DB volumes
```

---

## LightRAG Retrieval API

The `kb-backend` exposes the LightRAG retrieval pipeline over HTTP. Use it to fetch the top-ranked
Gold-layer chunks for a query and feed them to your own LLM for RAG.

**Base URL:** `http://localhost:8000` (the **Kong** gateway). These routes are fail-closed — call them
through Kong with a Keycloak bearer token, not `kb-backend` directly (`:8050` returns `401`).

| Method & path | Purpose |
|---|---|
| `POST /api/pipeline/light-rag/retrieve` | Run the full pipeline, return ranked chunks (JSON) |
| `POST /api/pipeline/light-rag/retrieve/stream` | Same, streamed as SSE — one event per step (progress UIs) |
| `GET  /api/pipeline/light-rag/health` | `{"status":"ok"}` when the pipeline is wired (also auth-gated) |

### Request

Identity (tenant, user, role) is **not** sent as headers — it comes from the Keycloak JWT. Kong
verifies the token and injects the authoritative `X-*` identity; the client sends only a bearer token.
Get one via the password grant (helios-* users have no required actions):

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d grant_type=password -d client_id=aeroflow-frontend \
  -d username=helios-engineer -d password='H3lios@Eng001!' | jq -r .access_token)
```

Body fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string | — | **required** — the natural-language question |
| `knowledge_base` | string | `""` (all) | restrict retrieval to one knowledge base |
| `collection` | string | `knowledge` | chunk collection to search |
| `tenant_id` | string | from the JWT | overrides the token's tenant |
| `entities` | string[] | `[]` | front-layer entities — skips the internal canonicalize LLM and uses these as the pass-1 filter + graph seeds |
| `intent` | string | `""` | front-layer query intent label |
| `entity_types` | object | `{}` | `{name: type}` e.g. `{"Việt Minh":"organization"}` — per-entity typed search |

```bash
curl -X POST http://localhost:8000/api/pipeline/light-rag/retrieve \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
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

The Knowledge Base UI is a Vite dev server. Its data layer (`src/lib/apiClient.ts`) is a thin fetch
wrapper around the **real KB FastAPI backend** reached **through Kong** — there is no standalone mock
server and no demo-auth bypass: the app always authenticates against Keycloak and sends a bearer token
(Kong injects the authoritative identity). Bring up a stack with Keycloak + Kong first
(see [Docker stacks at a glance](#docker-stacks-at-a-glance)), then:

```powershell
npm install
npm run dev
```

The dev server talks to the backend via:

- `VITE_KEYCLOAK_URL` / `VITE_KEYCLOAK_REALM` / `VITE_KEYCLOAK_CLIENT` — Keycloak login
  (defaults: `http://localhost:8080`, `aeroflow`, `aeroflow-frontend`)
- `VITE_API_URL` — base URL for KB API calls (default `http://localhost:8000`, the **Kong** gateway —
  **not** `kb-backend` on `:8050`, which is fail-closed)

The defaults already match the kb_e2e stack, so plain `npm run dev` works against it;
override only if a service is on a different host/port.

Other scripts:

```powershell
npm run lint          # tsc --noEmit (type check)
npm run build         # production build
npm run test          # vitest run
```

| Service | URL |
|---|---|
| Frontend (Vite dev) | http://localhost:3000 |
| Keycloak (login) | http://localhost:8080 |
| KB API via Kong (`VITE_API_URL`) | http://localhost:8000 |
| kb-backend (direct — bypasses Kong = 401) | http://localhost:8050 |

---

## Full Stack — Docker

The Docker stack lives under [`infra/`](./infra). The **base file is the single source
of truth** — every service lives there, tagged with a **profile** (`kb`, `runtime`,
`observability`, `iam`, `release`, `models`, …). Pick the layer you need with
`--profile`, not a separate `-f` file:

```bash
docker compose -f infra/docker-compose.yml --profile kb up -d               # KB data plane
docker compose -f infra/docker-compose.yml --profile observability up -d    # Jaeger + ES + Kibana + Filebeat + Langfuse
```

> The old per-concern split files (`storage` / `messaging` / `observability`) were
> folded into the base file's profiles — see git history if you need the diff.

Compose files (all under [`infra/`](./infra)):

- [`docker-compose.yml`](./infra/docker-compose.yml): **base / source of truth** — every service, gated by profiles
- [`docker-compose.app.yml`](./infra/docker-compose.app.yml): application + frontend tier (also profile-gated)
- [`docker-compose.security.yml`](./infra/docker-compose.security.yml): overlay — HA Keycloak (2 nodes + HAProxy) and hardened Kong admin
- [`docker-compose.prod.yml`](./infra/docker-compose.prod.yml): production hardening overlay — required secrets, TLS, WAL archiving + `postgres-backup` sidecar, `redis.conf`
- [`docker-compose.local.yml`](./infra/docker-compose.local.yml): local developer override (adds `migrate` + `seed`)
- [`docker-compose.dev.yml`](./infra/docker-compose.dev.yml): shared dev server override (adds `migrate` + `seed`)
- [`docker-compose.test.yml`](./infra/docker-compose.test.yml): test-only PostgreSQL fixture
- [`docker-compose.kb_e2e.yml`](./infra/docker-compose.kb_e2e.yml): isolated KB-UI integration stack — data plane + ingestion worker + Keycloak + DB-less Kong

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
| `infra/.env.kb-testing` | kb_e2e compose interpolation — MinIO credentials (the example defaults work as-is). The LLM / embed / rerank backend is configured separately on `model-service` via its `REMOTE_*_URL` env — see [Switching the model backend](./infra/README.md#switching-the-model-backend-local-cpu--colab-gpu). | `Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing` |
| `model/ollama/weights/model.gguf` (~5.7 GB) | the optional GPU `llama` service (mounted `../model/ollama/weights:/models`) | Download a GGUF model into that path. **Only** needed if you run the local `llama` service — by default the worker and LightRAG reach the LLM through the `model-service` `/generate` gateway (point its `REMOTE_LLM_URL` at the backend), and do not require it. |

Auto-managed (downloads itself — no manual file placement):

- **`infra/model-service/models/`** — where the embedding + reranker weights are saved. They are
  **not** baked into the model-service image; on the **first** `model-service` start they download
  here (~2.3 GB for the configured `EMBED_MODEL` + `RERANK_MODEL`) and persist across recreates, so
  later starts are instant. This keeps the image ~2.5 GB smaller. Consequences:
  - the first bring-up needs network and the model-service healthcheck has a 300 s `start_period`
    (kb-backend waits for it), so first start is slower — this is expected, not an error;
  - to pre-seed offline (skip the first-run download), populate the folder from another machine's
    `infra/model-service/models/`, or run:
    `docker compose -f infra/docker-compose.kb_e2e.yml run --rm model-service python -c "import models, config as c; models.load(c.EMBED_MODEL, c.RERANK_MODEL)"`
- DB content lives in Docker **named volumes** (persist across image rebuilds) and is seeded by the
  tracked [`infra/sql/kb-seed.sql`](./infra/sql/kb-seed.sql) (KB seed, mounted by `docker-compose.kb_e2e.dev.yml`).

### KB-UI testing stack

Isolated stack (KB data plane + ingestion worker + Keycloak + DB-less Kong) for exercising the KB
backend against the UI. Full walkthrough — including the frontend env and login — is in
[Running the KB-UI — kb_e2e](#running-the-kb-ui--kb_e2e-full-local-dev) above:

```powershell
docker compose -f infra/docker-compose.kb_e2e.yml --env-file infra/.env.kb-testing up -d --build
curl http://localhost:8050/health   # -> {"status":"ok"} (direct probe; app traffic goes via Kong :8000)
```

Switch the model-service backend (LOCAL CPU vs. remote Colab GPU) by setting the
`REMOTE_*_URL` env on `model-service` and recreating that one container — no image
rebuild required. See [Switching the model backend](./infra/README.md#switching-the-model-backend-local-cpu--colab-gpu).

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

---

## Chạy trên VM / Dev Server (Linux)

### Shell alias (chạy 1 lần)

```bash
source ~/.bashrc   # nếu chưa load alias dc
# hoặc thêm thủ công vào ~/.bashrc:
# litellm's host-gateway (host.docker.internal) is already in the base file,
# so the old -f docker-compose.llm.yml overlay is no longer needed.
alias dc='docker compose -f ~/Data-Agent/docker/docker-compose.yml \
                         -f ~/Data-Agent/docker/docker-compose.local.yml'
```

---

### Khởi động llama-server (local LLM)

llama-server chạy trực tiếp trên host (không qua Docker), cần bật **trước** khi start stack:

```bash
# Tải model lần đầu (chỉ chạy 1 lần)
bash model/llama-server/download-model.sh

# Khởi động llama-server ở port 8088
bash model/llama-server/start-llama.sh &

# Kiểm tra
curl http://localhost:8088/health
```

Tắt llama-server:
```bash
pkill -f llama-server
```

---

### Khởi động full stack

```bash
dc up -d --build
```

---

### Sau khi stack up lần đầu — chạy Kong setup

Kong cần được config 1 lần sau khi start (CORS, routes, JWT plugin...):

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.local.yml \
  -f docker/docker-compose.security.yml \
  --profile setup run --rm kong-setup
```

> Cần chạy lại nếu `down -v` (xóa volume Kong DB).

---

### Cấp quyền cho runtime_writer (chạy 1 lần sau migrate)

```bash
docker exec -i dataagent-postgres psql -U admin -d dataagent \
  < infra/sql/grant-runtime-writer.sql
```

---

### Rebuild 1 service

```bash
dc up -d --build workflow-runtime
dc up -d --build flow-builder
dc up -d --build litellm
```

---

### Tắt stack

Giữ data:
```bash
dc down
```

Xóa toàn bộ volume (fresh start):
```bash
dc down -v
```

---

### URLs khi chạy trên VM (thay `localhost` bằng IP VM)

| Service | URL |
|---|---|
| Frontend | http://10.10.55.11:3000 |
| Kong proxy | http://10.10.55.11:8000 |
| Langfuse | http://10.10.55.11:3001 |
| llama-server | http://10.10.55.11:8088 |

The local and shared-dev overrides both add `migrate` and `seed` jobs.

Separate intent-specific Docker paths also exist:

- Test-only: [`infra/docker-compose.test.yml`](./infra/docker-compose.test.yml)
- KB-UI integration: [`infra/docker-compose.kb_e2e.yml`](./infra/docker-compose.kb_e2e.yml)
- Standalone model experiment: [`model/ollama/docker/docker-compose.yml`](./model/ollama/docker/docker-compose.yml)

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
