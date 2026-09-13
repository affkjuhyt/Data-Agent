# GTEL Data Agent — AeroFlow AI OS

Multi-tenant Enterprise AI OS for RAG orchestration: a React Knowledge Base UI on top of a
workflow runtime, flow builder, knowledge-base backend, auth/gateway services, storage layers,
and observability tooling.

The active focus of this repository is the **Knowledge Operations Center** frontend under
[`frontend/src/components/knowledge/`](./frontend/src/components/knowledge), backed by the `kb-backend` service and
the `kb_ingestion_worker`.

## Repository Layout

One top-level folder per concern — a team edits its own tree without touching the others:

```
backend/     Python import root: services/ (16 FastAPI/worker services), libs/ (shared layer), alembic/ (migrations)
frontend/    React 19 + Vite app (npm workspace): src/, index.html, vite/vitest/tsconfig, Dockerfile.dev
deploy/      k8s/ (manifests: database/ service/ job/) · docker/ (Dockerfiles + service config) · ansible/ (superseded)
tests/       unit/ · integration/ · e2e/ (real-stack runner + Playwright) · loadtest/ (k6)
docs/        RUNBOOK · SECURITY · AGENT-E2E · KB-SCALE-10K · M3-CAPACITY
model/       LLM eval harness, RAGAS
```

| Path | Contents |
|---|---|
| [`frontend/src/`](./frontend/src) | React 19 + TypeScript + Vite frontend (Knowledge Base UI + Ask). `frontend/src/lib/apiClient.ts` is the FastAPI client (bearer-only — Kong injects identity). Root `package.json` is an npm workspace that forwards `npm run dev/test/typecheck` here |
| [`backend/services/`](./backend/services) | Backend microservices: `kb_backend`, `kb_ingestion_worker`, `auth_api`, `flow_builder`, `workflow_runtime` (which also hosts the multi-agent orchestrator at `/api/orchestrate`), `model_service`, `sandbox_executor`, `release_worker`, `platform_workers` (one image; `WORKER=memory\|schema-crawler\|audit` — ex memory-consumer, schema-crawler-worker, audit-bridge + audit-consumer). Each owns its `Dockerfile` (build context = repo root) |
| [`backend/libs/`](./backend/libs) | Shared cross-service layer: `libs/basemodel` (domain models/ORM), `libs/database_connector` (infra clients), `libs/shared` (logger, auth, observability), `libs/semantic` (metric contracts). Innermost architecture layer |
| [`backend/alembic/`](./backend/alembic) | Database migrations (`alembic -c backend/alembic/alembic.ini upgrade head`) |
| [`deploy/docker/`](./deploy/docker) | Dockerfiles and service config (`kong`, `kafka`, `keycloak`, `openbao`, `litellm`, `sql`, `certs`…) — build contexts and ConfigMap sources, no stack definition |
| [`deploy/ansible/`](./deploy/ansible) · [`deploy/k8s/`](./deploy/k8s) | Ansible playbooks (Kubernetes setup + rolling updates) · raw Kubernetes manifests |
| [`tests/`](./tests) | `pytest` unit/integration (`pythonpath = [".", "backend"]`), `tests/e2e/e2e.sh` full-stack runner + Playwright UI test, `tests/loadtest` k6 |
| [`docs/RUNBOOK.md`](./docs/RUNBOOK.md) · [`docs/SECURITY.md`](./docs/SECURITY.md) | Deploy/operate runbook · security posture & release hardening |

---

## Deployment at a glance

There is no mock server — the frontend talks to the real `kb-backend`, which is **fail-closed**:
every API request must arrive through **Kong** carrying a verified Keycloak identity (`X-Kong-Verified`),
or it is rejected with `401`. So a usable stack needs **Keycloak** (login) + **Kong** (gateway) in
front of `kb-backend`. Pick the stack that matches your task:

| Tier | Path | What it is |
|---|---|---|
| **database** | `deploy/k8s/production/database/` | StatefulSets: Postgres, Mongo, Redis, Qdrant, Neo4j, Kafka, MinIO, ClickHouse, Elasticsearch, OpenBao + PgBouncer |
| **service** | `deploy/k8s/production/service/` | Deployments: Kong, Keycloak, the app tier, the workers, observability |
| **job** | `deploy/k8s/production/job/` | `migrate`, `db-init`, `seed`, `minio-init`, `kong-migration`, `setup`, `kafka-topics`, `eval` + the backup CronJob |

One manifest per workload. [`deploy/k8s/README.md`](./deploy/k8s/README.md) has the
apply order, the Secret, and the ConfigMaps built from `deploy/docker/`.

> `kb-backend` is also published on `:8220` — but hitting it directly bypasses Kong, so it returns
> `401` by design (handy to confirm fail-closed behaviour).

---

## Running the KB-UI — kb-e2e (full local dev)

End-to-end local development with the Vite dev server, the ingestion pipeline, and the auth chain.

### 1. One-time setup

```powershell
npm install                                                # frontend deps
kubectl apply -f deploy/k8s/production/namespace.yaml
kubectl apply -f deploy/k8s/production/configmap.yaml -f deploy/k8s/production/networkpolicy.yaml
```

Then create `dataagent-secrets` and the file-backed ConfigMaps — the exact commands
are in [`deploy/k8s/README.md`](./deploy/k8s/README.md).

### 2. Start the stack

Stores first, then the migration jobs, then the services. Ports below are what a
`kubectl port-forward` gives you; in a real cluster an Ingress fronts Kong.

```powershell
kubectl apply -f deploy/k8s/production/database/
kubectl apply -f deploy/k8s/production/service/
```

> `model_service` is a pure API gateway since 04-09-2026 — all inference runs on the
> GPU server, no local weights to download.

### 3. Verify the stack is healthy

```powershell
kubectl -n dataagent get pods              # every pod Running/Ready
curl http://localhost:8220/health                               # -> {"status":"ok"} (direct; bypasses Kong)
```

### 4. Start the frontend

The UI logs in through Keycloak and sends API traffic through Kong (`:8100`), so point it there:

```powershell
$env:VITE_KEYCLOAK_URL    = "http://localhost:8110"
$env:VITE_KEYCLOAK_REALM  = "aeroflow"
$env:VITE_KEYCLOAK_CLIENT = "aeroflow-frontend"
$env:VITE_API_URL         = "http://localhost:8100"   # Kong proxy — NOT :8220
npm run dev                                            # Vite dev server on http://localhost:3000
```

Open http://localhost:3000, log in as **`helios-admin` / `H3lios@Admin01!`**, and the UI talks to
`kb-backend` through Kong.

### Shutdown

```powershell
kubectl delete -f deploy/k8s/production/service/        # stop, keep PVCs
kubectl -n dataagent delete pvc --all        # also wipe the data
```

---

## LightRAG Retrieval API

The `kb-backend` exposes the LightRAG retrieval pipeline over HTTP. Use it to fetch the top-ranked
Gold-layer chunks for a query and feed them to your own LLM for RAG.

**Base URL:** `http://localhost:8100` (the **Kong** gateway). These routes are fail-closed — call them
through Kong with a Keycloak bearer token, not `kb-backend` directly (`:8220` returns `401`).

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
TOKEN=$(curl -s -X POST http://localhost:8110/realms/aeroflow/protocol/openid-connect/token \
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
curl -X POST http://localhost:8100/api/pipeline/light-rag/retrieve \
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

The Knowledge Base UI is a Vite dev server. Its data layer (`frontend/src/lib/apiClient.ts`) is a thin fetch
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
  (defaults: `http://localhost:8110`, `aeroflow`, `aeroflow-frontend`)
- `VITE_API_URL` — base URL for KB API calls (default `http://localhost:8100`, the **Kong** gateway —
  **not** `kb-backend` on `:8220`, which is fail-closed)

The defaults already match the kb-e2e stack, so plain `npm run dev` works against it;
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
| Keycloak (login) | http://localhost:8110 |
| KB API via Kong (`VITE_API_URL`) | http://localhost:8100 |
| kb-backend (direct — bypasses Kong = 401) | http://localhost:8220 |

---

## Full Stack — Docker

The Docker stack lives under [`deploy/docker/`](./deploy/docker). Compose interpolates `${…}` from the
`.env` in its *project directory*, and the real file is the repo-root `.env` — so either run with
`--env-file .env` from the repo root (what `tests/e2e/e2e.sh` and the Ansible playbook do) or keep the
`deploy/docker/.env -> ../../.env` symlink the `preflight` role creates. The **base file is the single source
of truth** — every service lives there, tagged with a **profile** (`kb`, `runtime`,
`observability`, `iam`, `release`, `models`, …). Pick the layer you need with
`--profile`, not a separate `-f` file:

```bash
kubectl apply -f deploy/k8s/production/service/         # app tier
kubectl -n dataagent get hpa                 # autoscaling status
```

> The base file and its twelve overlays were replaced by one file per service —
> the originals are kept in [`deploy/docker/.old/`](./deploy/docker/.old).

Compose files live in [`deploy/docker/`](./deploy/docker), one per service:
`database/`, `service/` and `job/`, one manifest per workload, and
`overlay-*.yml` for the two patches (host-published Kong admin, dev bind mounts).
Environment differences that used to be overlay files — production hardening,
small-RAM caps, dev tuning — are environment variables now; see
[`deploy/docker/README.md`](./deploy/docker/README.md).

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
- `AUDIT_CONSUMER_KAFKA_PASSWORD`
- `RELEASE_WORKER_KAFKA_PASSWORD`

### Local-only files (not baked into images or git)

The kb-backend image ([`backend/services/kb_backend/Dockerfile`](./backend/services/kb_backend/Dockerfile)) bakes in **only application code**
— `basemodel/` and `backend/services/`. It contains no secrets, datasets, model weights, or DB seed data;
those are supplied at runtime via environment variables, host bind-mounts, and Docker volumes.

A few of those inputs are **gitignored**, so a fresh clone will not have them and must recreate them
before bringing the KB-UI testing stack up:

| Local-only file | Used by | What to do |
|---|---|---|
| `deploy/docker/.env.kb-testing` | kb-e2e compose interpolation — MinIO credentials (the example defaults work as-is). The LLM / embed / rerank backend is configured separately on `model_service` via its `REMOTE_*_URL` env — see [Model backend](./deploy/docker/README.md#model-backend-gpu-server). | `Copy-Item deploy/docker/.env.kb-testing.example deploy/docker/.env.kb-testing` |

Auto-managed (downloads itself — no manual file placement):

- model weights: none locally — embed/rerank/LLM đều chạy trên GPU server.

- DB content lives in Docker **named volumes** (persist across image rebuilds) and is seeded by the
  tracked [`deploy/docker/sql/kb-seed.sql`](./deploy/docker/sql/kb-seed.sql) (KB seed, applied by `deploy/k8s/production/job/db-init.yaml`).

### KB-UI testing stack

Isolated stack (KB data plane + ingestion worker + Keycloak + DB-less Kong) for exercising the KB
backend against the UI. Full walkthrough — including the frontend env and login — is in
[Running the KB-UI — kb-e2e](#running-the-kb-ui--kb-e2e-full-local-dev) above:

```powershell
kubectl apply -f deploy/k8s/production/service/
curl http://localhost:8220/health   # -> {"status":"ok"} (direct probe; app traffic goes via Kong :8100)
```

The model backend is the GPU server; change `REMOTE_*_URL` in `.env` and recreate `model_service` — no image
rebuild required.

### Official Docker Commands

Run all commands from the repository root.

#### Local machine

Start or refresh the full local stack:

```powershell
kubectl apply -f deploy/k8s/production/database/
```

Stop the local stack and keep data:

```powershell
kubectl delete -f deploy/k8s/production/service/
```

Stop the local stack and remove volumes:

```powershell
kubectl -n dataagent delete pvc --all
```

#### Shared dev server

```powershell
kubectl apply -f deploy/k8s/production/database/
kubectl delete -f deploy/k8s/production/service/
```

#### Compose validation

```powershell
kubectl apply --dry-run=client -R -f deploy/k8s/
kubectl apply --dry-run=client -R -f deploy/k8s/
```

#### Test fixture

Start the isolated PostgreSQL fixture used by connector and Alembic tests:

```powershell
# integration tests spin Postgres up through testcontainers
pytest -m integration
```

### Runtime Shape

The base stack includes these service groups:

- **Storage:** PostgreSQL, MongoDB, Redis, MinIO
- **Search and graph:** Qdrant, Neo4j
- **Security and gateway:** Keycloak, Kong, OpenBao
- **Messaging:** Kafka
- **Observability and AI tooling:** LiteLLM, Langfuse, Jaeger, Elasticsearch
- **Application services:** workflow_runtime, flow_builder, kb-backend, kb_ingestion_worker, model_service, auth_api, release_worker, platform_workers (memory / schema-crawler / audit) (JWKS/JWT verification is handled at the edge by the Kong `aeroflow-jwks` plugin, not a service)
- **Frontend:** Vite server on `5173`

---

## Chạy trên VM / Dev Server (Linux)

### Shell alias (chạy 1 lần)

```bash
source ~/.bashrc   # nếu chưa load alias dc
# hoặc thêm thủ công vào ~/.bashrc:
alias k='kubectl -n dataagent'
```

---


---

### Khởi động full stack

```bash
dc up -d --build
```

---

### Sau khi stack up lần đầu — chạy Kong setup

Kong cần được config 1 lần sau khi start (CORS, routes, JWT plugin...):

```bash
kubectl apply -f deploy/k8s/production/job/setup.yaml
```

> Cần chạy lại nếu `down -v` (xóa volume Kong DB).

---

### Cấp quyền cho app_user / runtime_writer (chạy 1 lần sau migrate)

Trên k8s, `job/db-init.yaml` chạy bước này và đặt mật khẩu từ Secret.
Chạy tay thì phải đặt mật khẩu cùng lúc, nếu không role không đăng nhập được:

```bash
docker exec -i dataagent-postgres psql -U admin -d dataagent \
  < deploy/docker/sql/grant-roles.sql
docker exec -i dataagent-postgres psql -U admin -d dataagent \
  -c "ALTER ROLE app_user       WITH LOGIN PASSWORD '<APP_USER_PASSWORD>'" \
  -c "ALTER ROLE runtime_writer WITH LOGIN PASSWORD '<RUNTIME_WRITER_PASSWORD>'"
```

---

### Rebuild 1 service

```bash
dc up -d --build workflow_runtime
dc up -d --build flow_builder
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
| GPU model server | http://10.10.55.101:8001-8004 (vLLM main/fast, TEI embed/rerank) |

The one-shot jobs live in [`deploy/k8s/production/job/`](./deploy/k8s/production/job) — `migrate`, `db-init`, `seed`, `setup`.

Separate intent-specific Docker paths also exist:

- Integration fixtures: testcontainers (see `tests/integration/`)

### Common URLs (Docker stack)

Every host port is `8xxx`; `8000–8099` is reserved for the LLM tier and left
empty. Full map in [`deploy/docker/README.md`](./deploy/docker/README.md).

| Service | URL |
|---|---|
| Frontend | http://localhost:8150 |
| Kong proxy — **all API traffic** | http://localhost:8100 |
| Kong admin (loopback, opt-in overlay) | http://localhost:8101 |
| Keycloak | http://localhost:8110 |
| OpenBao | http://localhost:8120 |
| auth_api | http://localhost:8200 |
| release_worker | http://localhost:8210 |
| kb-backend (loopback, debug only) | http://localhost:8220 |
| model_service | http://localhost:8230 |
| Postgres / Mongo / Redis / Qdrant | 8300 / 8310 / 8320 / 8330 |
| Kafka / MinIO API + console | 8340 / 8350 + 8351 |
| Prometheus / Grafana / Langfuse | 8400 / 8410 / 8420 |
| Jaeger UI / Kibana | 8430 / 8440 |
| LiteLLM | http://localhost:8500 |

Not published — reachable only through Kong or in-network: `workflow_runtime`
(`:8240`), `flow_builder` (`:8250`), `sandbox_executor` (`:8260`), `llm_exporter`
(`:8270`), Neo4j (`:7474`/`:7687`).

### Notes

- `deploy/k8s` reads secrets from the `dataagent-secrets` Secret; `.env` is only used to build images and run tests locally.
- Container names are prefixed `dataagent-*` and share the `data-agent-network` Docker network.
- The Keycloak realm is `aeroflow`; the frontend authenticates against it via the `VITE_KEYCLOAK_*` variables.
- Neo4j is exposed only on the internal network (ports `7474`/`7687`) and is not published to the host by default.
