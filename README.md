# Data Agent

An enterprise data agent platform, deployed inside the customer's own infrastructure: it turns
scattered data (operational databases and warehouses, Excel, Vietnamese Word/PDF documents, web
sources) into certified metrics, evidence-backed answers and automated monitoring. Every answer
carries a trust label and opens to the SQL or document it came from.

Product direction, agent-core design, the R0 code fixes and the backlog start at
[`docs/v3/README.md`](./docs/v3/README.md). The Observation Engine (indicator series, as-of, data
quality, human review of numbers) and the oil/gold reference Data Space are specified in
[`docs/v2/README.md`](./docs/v2/README.md). Working rules for humans and agents: [`CLAUDE.md`](./CLAUDE.md).

## Repository Layout

One top-level folder per concern — a team edits its own tree without touching the others:

```
backend/     Python import root: services/ (FastAPI/worker services), libs/ (shared layer), alembic/ (migrations)
frontend/    React 19 + Vite app (npm workspace): src/, index.html, vite/vitest/tsconfig, Dockerfile.dev
deploy/      ansible/ (deploy pipeline: deploy.yml) · k8s/ (manifests: production/ and dev/) · docker/ (Dockerfiles + service config)
tests/       unit/<service> · integration/<service> · e2e/ (demo seed data, eval + Playwright harnesses) · loadtest/ (k6)
docs/        v3/ (product direction, agent core, R0 design, backlog) · v2/ (Observation Engine spec, reference Data Space, data/ fixtures) · RUNBOOK · SECURITY · SILVER-FORMAT
model/       eval/ (release gate: cases, scorers incl. RAGAS faithfulness, thresholds) · bench/ (serving latency)
```

| Path | Contents |
|---|---|
| [`frontend/src/`](./frontend/src) | React 19 + TypeScript + Vite frontend (Knowledge Base UI + Ask). `frontend/src/lib/apiClient.ts` is the FastAPI client (bearer-only — Kong injects identity). Root `package.json` is an npm workspace that forwards `npm run dev/test/typecheck` here |
| [`backend/services/`](./backend/services) | Backend microservices: `kb_backend` (API + the four KB Kafka workers under `workers/`, built as the separate `kb-worker` image from `deploy/docker/kb-worker`); `auth_api`, `workflow_runtime` (which also hosts the agent/workflow/canvas authoring API, ex flow_builder, and the multi-agent orchestrator at `/api/orchestrate`), `model_service`, `sandbox_executor`, `platform_workers` (one image; `WORKER=memory\|schema-crawler\|audit\|web-crawler`). Each owns its `Dockerfile` (build context = repo root) |
| [`backend/libs/`](./backend/libs) | Shared cross-service layer: `libs/basemodel` (domain models/ORM), `libs/database_connector` (infra clients), `libs/shared` (logger, auth, observability), `libs/ingestion` (Bronze→Silver→Gold pipeline the KB workers run), `libs/semantic` (metric contracts), `libs/indicator` (numeric core). Innermost architecture layer |
| [`backend/alembic/`](./backend/alembic) | Database migrations (`alembic -c backend/alembic/alembic.ini upgrade head`) |
| [`deploy/ansible/`](./deploy/ansible) | The deploy pipeline — `deploy.yml` builds what changed, renders Secrets from the vault, applies the manifests, reruns Jobs, restarts what changed |
| [`deploy/k8s/`](./deploy/k8s) | Kubernetes manifests: `production/` (hardened) and `dev/` (the single-node dev server) |
| [`deploy/docker/`](./deploy/docker) | Image build contexts (`keycloak`, `setup`) and the files the k8s ConfigMaps and Jobs read (`kong`, `sql`, `litellm`, `monitoring`…) |
| [`tests/`](./tests) | `pytest` unit/integration, one suite per service (`pythonpath = [".", "backend"]`), e2e tests against the dev cluster, `tests/e2e/` demo seed data + eval/Playwright harnesses, `tests/loadtest` k6 — see [`tests/README.md`](./tests/README.md) |
| [`docs/RUNBOOK.md`](./docs/RUNBOOK.md) · [`docs/SECURITY.md`](./docs/SECURITY.md) | Operating the stack · security posture & release hardening |

---

## Deployment at a glance

There is no mock server — the frontend talks to the real `kb-backend`, which is **fail-closed**:
every API request must arrive through **Kong** carrying a verified Keycloak identity (`X-Kong-Verified`),
or it is rejected with `401`. So a usable stack needs **Keycloak** (login) + **Kong** (gateway) in
front of `kb-backend`.

The stack runs on Kubernetes. The shared dev server (`10.10.55.101`, single-node k3s) is deployed
from `/home/ndip/Data-Agent` with the Ansible pipeline:

```bash
git commit ...                                   # only committed changes are deployed
cd deploy/ansible && ansible-playbook deploy.yml
```

| Dev server | URL |
|---|---|
| Frontend | http://10.10.55.101:30280 |
| Kong proxy — all API traffic | http://10.10.55.101:30100 |
| Keycloak (realm `aeroflow`) | http://10.10.55.101:30110 |
| Langfuse | http://10.10.55.101:30420 |

| Read | For |
|---|---|
| [`docs/RUNBOOK.md`](./docs/RUNBOOK.md) | **Start here** — URLs, logins, deploying a change, demo data, OpenBao, disk, troubleshooting |
| [`deploy/ansible/README.md`](./deploy/ansible/README.md) | How the pipeline decides what to build/apply/restart, its flags, secrets, server-only state |
| [`deploy/k8s/README.md`](./deploy/k8s/README.md) | Manifest design: namespaces, network policy, ports, dev vs production |

---

## Frontend development

The Knowledge Base UI is a Vite app. Its data layer (`frontend/src/lib/apiClient.ts`) is a thin fetch
wrapper around the **real KB FastAPI backend** reached **through Kong** — no mock server and no
demo-auth bypass: the app always authenticates against Keycloak and sends a bearer token.

On the dev server the frontend pod already runs the Vite dev server. To run it on your own machine
against the dev server's backend:

```bash
npm install
export VITE_KEYCLOAK_URL=http://10.10.55.101:30110
export VITE_KEYCLOAK_REALM=aeroflow
export VITE_KEYCLOAK_CLIENT=aeroflow-frontend
export VITE_API_URL=http://10.10.55.101:30100     # Kong — NOT kb-backend
npm run dev                                        # http://localhost:3000
```

`http://localhost:3000` is already an allowed origin in Kong's CORS config and a redirect URI of the
Keycloak client. The built-in defaults (`localhost:8110` / `localhost:8100`) only work with
port-forwards. App users are listed in [RUNBOOK §2](./docs/RUNBOOK.md#2-urls-and-logins).

```bash
npm run build         # production build
npm run test          # vitest run
```

---

## LightRAG Retrieval API

The `kb-backend` exposes the LightRAG retrieval pipeline over HTTP. Use it to fetch the top-ranked
Gold-layer chunks for a query and feed them to your own LLM for RAG.

**Base URL:** the **Kong** gateway — `http://10.10.55.101:30100` on the dev server. These routes are
fail-closed — call them through Kong with a Keycloak bearer token, not `kb-backend` directly (it returns `401`).

| Method & path | Purpose |
|---|---|
| `POST /api/pipeline/light-rag/retrieve` | Run the full pipeline, return ranked chunks (JSON) |

### Request

Identity (tenant, user, role) is **not** sent as headers — it comes from the Keycloak JWT. Kong
verifies the token and injects the authoritative `X-*` identity; the client sends only a bearer token.
Get one via the password grant (helios-* users have no required actions):

```bash
KC=http://10.10.55.101:30110; API=http://10.10.55.101:30100
TOKEN=$(curl -s -X POST $KC/realms/aeroflow/protocol/openid-connect/token \
  -d grant_type=password -d client_id=aeroflow-frontend \
  -d username=helios-engineer --data-urlencode "password=$PASSWORD" | jq -r .access_token)
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
curl -X POST $API/api/pipeline/light-rag/retrieve \
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

---

## Runtime shape

- **Storage:** PostgreSQL (+ PgBouncer), MongoDB, Redis, MinIO
- **Search and graph:** Qdrant, Neo4j, ClickHouse
- **Security and gateway:** Keycloak, Kong (`aeroflow-jwks` plugin verifies JWTs at the edge), OpenBao
- **Messaging:** Kafka
- **Observability and AI tooling:** LiteLLM, Langfuse, Prometheus, Grafana, Jaeger (no central log store: `kubectl logs`)
- **Application services:** workflow_runtime, kb-backend, kb-promote-silver / kb-promote-gold / kb-structured-data / kb-conflict-resolution (four Deployments of the one kb-worker image), model_service, auth_api, sandbox_executor, platform_workers (memory / schema-crawler / audit / web-crawler / llm-exporter)
- **LLM tier:** GPU services on `10.10.55.101` — vLLM main `:8001` (`agent-main`), vLLM fast `:8002` (`agent-fast`), TEI embed `:8003`, TEI rerank `:8004`

Every workload listens on an `8xxx` port; `8000–8099` is reserved for the LLM tier. The full port
table is in [`deploy/k8s/README.md`](./deploy/k8s/README.md#ports).

## Tests

Tests run inside the service images, never with the host Python (3.10; the code targets 3.11):

```bash
make test-unit          # one pytest suite per service, each in that service's image
make test-integration   # testcontainers suites (Postgres, Qdrant, MinIO…)
make e2e                # pytest -m e2e against the deployed dev cluster — writes data, see tests/README.md
make frontend-test      # vitest
```

`make help` lists the rest; [`CLAUDE.md`](./CLAUDE.md) has the command surface and repo rules.
