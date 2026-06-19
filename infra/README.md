# infra — Docker stacks & setup

This directory holds every Docker Compose stack, the service Dockerfiles, and the
infra services (`docling`, `model-service`, `kb-ingestion-worker`, `kong`,
`Postgres`, `Neo4j`, `Qdrant`, `kafka`, `keycloak`). This is the one place that
documents how to bring a stack up. Run all commands from the **repository root**.

The frontend has no mock server and no auth bypass: `kb-backend` is **fail-closed**
— every API request must arrive through **Kong** with a verified Keycloak identity
(`X-Kong-Verified`) or it is rejected with `401`. So a usable stack always includes
**Keycloak** (login) + **Kong** (gateway) in front of `kb-backend`.

## Which stack?

| Stack | File / helper | What it is | When to use |
|---|---|---|---|
| **kb_and_authen** | [`docker-compose.kb_and_authen.yml`](./docker-compose.kb_and_authen.yml) + [`kb_and_authen.ps1`](./kb_and_authen.ps1) | Full auth chain + live KB data plane, **memory-capped to ~6.4 GiB** (fits an ~8 GiB Docker VM). Helper builds, waits for health, configures Kong, runs verify checks. Ingestion worker is OFF by default (behind the `pipeline` profile). | Quickest path to a working UI; low-RAM end-to-end auth/data-plane check; CI-style smoke test. **Recommended.** |
| **kb_e2e** | [`docker-compose.kb_e2e.yml`](./docker-compose.kb_e2e.yml) | Same auth chain + data plane + ingestion worker, with richer LLM options (remote vLLM / local `llama` profile) and multilingual models. | Day-to-day local dev with the Vite UI and the full ingestion/RAG pipeline. |
| **Full platform** | [`docker-compose.yml`](./docker-compose.yml) + an override | The whole Data Agent OS — every service group (storage, security, messaging, observability, all app services), composed from the layer files below. | Running the entire platform, not just KB. |
| **Test fixture** | [`docker-compose.test.yml`](./docker-compose.test.yml) | Isolated PostgreSQL only. | Connector / Alembic tests. |

> kb_and_authen and kb_e2e both publish `8000`/`8080`/`5432`/… so **run one at a time**.
> `kb-backend` is also published on `:8050` in each — hitting it directly bypasses Kong, so
> it returns `401` by design (handy to confirm fail-closed behaviour).

---

## Quick start — kb_and_authen (recommended)

```powershell
npm install                               # frontend deps (first time)
pwsh infra/kb_and_authen.ps1              # build + start + configure Kong (ingestion worker OFF)
pwsh infra/kb_and_authen.ps1 verify       # prove the chain: 401 / 200 / fleet 200 / 403 / 200
pwsh infra/kb_and_authen.ps1 up -WithUi   # also start the containerized frontend on :5173
```

Log in via Keycloak as **`helios-admin` / `H3lios@Admin01!`** (full perms) or
**`helios-engineer` / `H3lios@Eng001!`** (limited — write actions return `403`).

| Action | Command |
|---|---|
| Stop, keep data | `pwsh infra/kb_and_authen.ps1 down` |
| Stop, **wipe** data | `pwsh infra/kb_and_authen.ps1 destroy` |
| Tail logs | `pwsh infra/kb_and_authen.ps1 logs` |
| Enable bronze→silver→gold pipeline | `pwsh infra/kb_and_authen.ps1 up -WithPipeline` (needs ~12 GiB WSL VM) |

---

## Full local dev — kb_e2e

Brings up Postgres, Qdrant, Neo4j, MongoDB, MinIO, Kafka, model-service, `kb-backend`,
`kb-ingestion-worker`, **Keycloak** (`:8080`), and a DB-less **Kong** gateway (`:8000`).
Postgres tables (incl. a dedicated `keycloak` DB), Qdrant collections, and the Kong
route/JWT plugin are all created automatically on first start.

```powershell
npm install
Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing   # gitignored env; defaults work locally
docker compose -f infra/docker-compose.kb_e2e.yml --env-file infra/.env.kb-testing up -d --build
docker compose -f infra/docker-compose.kb_e2e.yml ps            # every service "healthy"
curl http://localhost:8050/health                               # -> {"status":"ok"} (direct; bypasses Kong)
```

Point the Vite dev server at Keycloak + Kong, then `npm run dev` (http://localhost:3000):

```powershell
$env:VITE_KEYCLOAK_URL    = "http://localhost:8080"
$env:VITE_KEYCLOAK_REALM  = "aeroflow"
$env:VITE_KEYCLOAK_CLIENT = "aeroflow-frontend"
$env:VITE_API_URL         = "http://localhost:8000"   # Kong proxy — NOT :8050
npm run dev
```

Shutdown: `docker compose -f infra/docker-compose.kb_e2e.yml down` (`-v` also wipes volumes).

> First start is slower: `model-service` downloads its embed/rerank weights into
> `infra/model-service/models/` (~2.3 GB, persisted for next time).

---

## Full platform stack

The base [`docker-compose.yml`](./docker-compose.yml) is split into service-group layer
files; an override adds the `migrate` + `seed` jobs. Run from the repo root with a real
`.env` (`Copy-Item .env.example .env`).

| Layer file | Contents |
|---|---|
| [`docker-compose.storage.yml`](./docker-compose.storage.yml) | PostgreSQL · MongoDB · Redis · MinIO · Qdrant · Neo4j |
| [`docker-compose.security.yml`](./docker-compose.security.yml) | Keycloak (HA) · Kong · OpenBao · HAProxy |
| [`docker-compose.messaging.yml`](./docker-compose.messaging.yml) | Kafka |
| [`docker-compose.observability.yml`](./docker-compose.observability.yml) | Elasticsearch · Jaeger · Langfuse · LiteLLM |
| [`docker-compose.app.yml`](./docker-compose.app.yml) | workflow-runtime · flow-builder · kb-backend · model-service · auth-api · … |
| [`docker-compose.local.yml`](./docker-compose.local.yml) | local override (adds `migrate` + `seed`) |
| [`docker-compose.dev.yml`](./docker-compose.dev.yml) | shared dev-server override (adds `migrate` + `seed`) |

```powershell
# local
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --build
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml down      # -v to wipe
# shared dev server
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d --build
# validate without starting
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml config
# test fixture (isolated Postgres on :5433)
docker compose -f infra/docker-compose.test.yml up -d
```

---

## Switching the model backend (local CPU ↔ Colab GPU)

`model-service` is the single model gateway. Everything that needs a model — the
ingestion worker and the LightRAG retrieval pipeline — calls only its three endpoints
and never knows which backend serves the call:

| Endpoint | Used for | Callers |
|---|---|---|
| `POST /embed` | text → vector | LightRAG query-embed, reembed, gold/silver |
| `POST /rerank` | rerank candidates | LightRAG rerank step |
| `POST /generate` | LLM text / JSON | LightRAG `canonicalize`, ingestion `llm_client` |

Behind those endpoints, model-service holds a **backend provider registry**
([`model-service/backends/`](./model-service/backends/)): for each capability
(chat / embed / rerank) the active backend is the first registered one that is
`available()`. A **remote** backend is available when its `REMOTE_*_URL` env is set
(forwarding to e.g. a Colab GPU behind a cloudflared tunnel); otherwise the **local**
in-process CPU model serves. So switching is a single env change — no code, no caller change.

| env (on `model-service`) | effect |
|---|---|
| `REMOTE_EMBED_URL` | set → `/embed` forwards there; empty → local CPU model (`EMBED_MODEL`) |
| `REMOTE_RERANK_URL` | set → `/rerank` forwards there; empty → local CPU reranker |
| `REMOTE_LLM_URL` | set → `/generate` forwards to this OpenAI-compatible server (e.g. Colab vLLM); empty → 503 (no local LLM yet) |
| `REMOTE_API_KEY` / `REMOTE_LLM_KEY` | bearer keys for the embed/rerank and LLM servers |

### How to switch

Set the relevant `REMOTE_*_URL` (+ key) env on `model-service` and recreate just that
container — no rebuild, no other service restart:

```powershell
$env:REMOTE_EMBED_URL = "https://<embed-tunnel>.trycloudflare.com"; $env:REMOTE_API_KEY = "kb-emb-xxxx"
$env:REMOTE_LLM_URL   = "https://<vllm-tunnel>.trycloudflare.com";  $env:REMOTE_LLM_KEY = "kb-IT-xxxx"
docker compose -f infra/docker-compose.kb_e2e.yml up -d --no-deps model-service
```

To revert to local CPU, clear those vars and recreate `model-service` again. The Colab GPU
servers live in `model/embedding/embed_rerank_colab.ipynb` (embed + rerank) and
`model/vllm/vllm_qwen_colab.ipynb` (LLM) — each prints its `TUNNEL_URL` + `API_KEY`.

> **⚠️ The embed dim must match the index.** The embed model fixes the Qdrant vector
> dimension (the corpus is currently indexed at 1024-d). Switching **LLM** or **rerank**
> is safe any time. Switching **embed** to a different dim requires re-embedding first
> (`reembed.py`) or every search fails with a dim mismatch. Quick-tunnels rotate URLs on
> each Colab restart — just re-run `colab --embed-url <new> …`.

---

## Layout

```
infra/
  docker-compose.kb_and_authen.yml   kb_and_authen.ps1   # recommended KB stack + helper
  docker-compose.kb_e2e.yml                              # full local dev KB stack
  docker-compose.yml + {storage,security,messaging,      # full platform stack
    observability,app}.yml + {local,dev}.yml
  docker-compose.test.yml                                # isolated Postgres test fixture
  Dockerfile.kb           # kb-backend image
  Dockerfile.dev          # dev-only frontend container
  model-service/          docling/          kb-ingestion-worker/   # infra service images
  kong/  keycloak/  Postgres/  Neo4j/  Qdrant/  kafka/  openbao/  haproxy/  litellm/
  Qdrant/COLLECTIONS.md   # Qdrant collection schema reference
```
