# infra — Docker stacks & setup

This directory holds every Docker Compose stack, the service Dockerfiles, and the
infra services (`model-service`, `kb-ingestion-worker`, `kong`, `Postgres`, `Neo4j`,
`Qdrant`, `kafka`, `keycloak`). Run all commands from the **repository root**.

The frontend has no mock server and no auth bypass: `kb-backend` is **fail-closed**
— every API request must arrive through **Kong** with a verified Keycloak identity
(`X-Kong-Verified`) or it is rejected with `401`. So a usable stack always includes
**Keycloak** (login) + **Kong** (gateway) in front of `kb-backend`.

## Which stack?

| Stack | File / helper | What it is | When to use |
|---|---|---|---|
| **kb_and_authen** | [`docker-compose.kb_and_authen.yml`](./docker-compose.kb_and_authen.yml) + [`kb_and_authen.ps1`](./kb_and_authen.ps1) | Standalone self-contained stack: full auth chain + KB data plane, **memory-capped to ~6.4 GiB**. Includes Kong setup. Ingestion worker behind the `pipeline` profile. | Quickest path to a working UI; low-RAM smoke test. **Recommended for first-time setup.** |
| **kb_e2e** | [`docker-compose.yml`](./docker-compose.yml) + [`docker-compose.kb_e2e.yml`](./docker-compose.kb_e2e.yml) + [`kb_e2e.ps1`](./kb_e2e.ps1) | **Overlay** on the main platform compose. Shares the same `dataagent` DB, Kafka internal listener (`kafka:9094`), and network as the full platform. Adds dev-friendly memory limits on Neo4j and KB tuning knobs. | Day-to-day local dev; testing KB against the real platform infra; pre-merge validation. |
| **Full platform** | [`docker-compose.yml`](./docker-compose.yml) + an override | The whole Data Agent OS — every service group (storage, security, messaging, observability, all app services). | Running the entire platform, not just KB. |
| **Test fixture** | [`docker-compose.test.yml`](./docker-compose.test.yml) | Isolated PostgreSQL only. | Connector / ORM tests. |

> `kb_and_authen` and `kb_e2e` both publish `8000`/`8080`/`5432`/… — **run one at a time**.
> `kb-backend` is also published on `:8050` — hitting it directly bypasses Kong and returns
> `401` by design (handy to confirm fail-closed behaviour).

---

## Quick start — kb_and_authen (recommended)

```powershell
npm install                               # frontend deps (first time only)
pwsh infra/kb_and_authen.ps1              # build + start + configure Kong
pwsh infra/kb_and_authen.ps1 verify       # prove the chain: 401 / 200 / 403 / 200
pwsh infra/kb_and_authen.ps1 up -WithUi   # also start the containerised frontend on :5173
```

Log in via Keycloak as **`helios-admin` / `H3lios@Admin01!`** (full perms) or
**`helios-engineer` / `H3lios@Eng001!`** (limited — write actions return `403`).

All six dev users and their passwords:

| Username | Password | Role |
|---|---|---|
| `helios-admin` | `H3lios@Admin01!` | `platform-admin` (all perms) |
| `helios-engineer` | `H3lios@Eng001!` | `ai-engineer` |
| `helios-viewer` | `H3lios@View001!` | `executive-viewer` |
| `platform-admin` | `Platform@Admin01!` | `platform-admin` |
| `ai-engineer` | `AiEng@001!` | `ai-engineer` |
| `executive-viewer` | `Exec@View001!` | `executive-viewer` |

All users share tenant `a0000000-0000-0000-0000-000000000001` (the seeded dev tenant).

| Action | Command |
|---|---|
| Stop, keep data | `pwsh infra/kb_and_authen.ps1 down` |
| Stop, **wipe** data | `pwsh infra/kb_and_authen.ps1 destroy` |
| Tail logs | `pwsh infra/kb_and_authen.ps1 logs` |
| Enable ingestion pipeline | `pwsh infra/kb_and_authen.ps1 up -WithPipeline` (~12 GiB WSL VM) |

---

## Full local dev — kb_e2e (overlay)

`docker-compose.kb_e2e.yml` is a **compose overlay** — it is not standalone. It always
runs on top of `docker-compose.yml` which provides the shared platform infrastructure.

```
docker-compose.yml         →  postgres · mongo · qdrant · neo4j · minio · kafka
                              keycloak · kong-migration · kong · model-service
                              kb-backend · kb-ingestion-worker (base definitions)
docker-compose.kb_e2e.yml  →  overrides / extensions:
                                postgres      adds KB seed data (05-kb-seed.sql)
                                keycloak      adds healthcheck
                                neo4j         reduces memory to dev-friendly 1.5 GB
                                minio-init    creates the knowledge-base bucket
                                kong-setup    configures routes + JWT plugin
                                model-service adds remote GPU tunnel env vars
                                kb-backend    publishes :8050, adds LightRAG knobs
                                kb-ingestion-worker  adds docling cache volume
```

All services share the same `dataagent` Postgres database and Kafka plaintext listener
(`kafka:9094`) — identical to the full platform.

### Step 1 — create your env file (first time only)

```powershell
Copy-Item infra/.env.kb-testing.example infra/.env.kb-testing
```

Edit `infra/.env.kb-testing`:
- `REMOTE_LLM_URL` / `REMOTE_LLM_KEY` — Colab vLLM tunnel. Without it the gold
  ingestion stage and LightRAG canonicalize step return 503; bronze→silver and
  retrieval still work.
- `REMOTE_EMBED_URL` / `REMOTE_RERANK_URL` / `REMOTE_API_KEY` — remote GPU for
  embed/rerank. When set, model-service skips the local model download entirely
  and starts in seconds.
- `KAFKA_ADMIN_PASSWORD` — required (no compose default). Set to `dev123` for local
  dev. Must match `infra/kafka/kafka-broker-jaas.config`.

The file ships `KAFKA_ADMIN_PASSWORD=dev123` and `POSTGRES_PASSWORD=dev123` as safe
local defaults.

### Step 2 — create the Kafka JAAS config (first time only)

```powershell
Copy-Item infra/kafka/kafka-broker-jaas.config.example infra/kafka/kafka-broker-jaas.config
# Then substitute the passwords — for local dev, replace all REPLACE_WITH_* with dev123
```

### Step 3 — start the stack

```powershell
# Helper (recommended) — starts only KB-relevant services:
pwsh infra/kb_e2e.ps1

# Or manually:
docker compose `
  -f infra/docker-compose.yml `
  -f infra/docker-compose.kb_e2e.yml `
  --env-file infra/.env.kb-testing `
  up -d --build `
  postgres mongo qdrant neo4j minio kafka keycloak `
  kong-migration kong kong-setup minio-init `
  model-service kb-backend kb-ingestion-worker
```

`kong-setup` starts automatically once Kong and Keycloak are healthy (~60–90 s on
first boot, faster on subsequent starts). Watch it:

```powershell
docker logs kb-kong-setup -f
```

Smoke-test the stack once `kong-setup` exits 0:

```powershell
curl http://localhost:8050/health   # {"status":"ok"}  — direct (bypasses Kong)
curl http://localhost:8000/health   # {"message":"no Route matched"} — Kong proxy up
curl http://localhost:8080/realms/aeroflow/.well-known/openid-configuration  # Keycloak realm
```

### Step 4 — start the Vite dev server (separate terminal)

```powershell
$env:VITE_KEYCLOAK_URL    = "http://localhost:8080"
$env:VITE_KEYCLOAK_REALM  = "aeroflow"
$env:VITE_KEYCLOAK_CLIENT = "aeroflow-frontend"
$env:VITE_API_URL         = "http://localhost:8000"   # Kong proxy — NOT :8050
npm run dev
```

Open **http://localhost:3000** and log in as `helios-admin` / `H3lios@Admin01!`.

> The Vite dev server is **not** part of the Docker stack — start it manually each
> session. Use `pwsh infra/kb_and_authen.ps1 up -WithUi` if you want a containerised
> frontend instead.

| Action | Command |
|---|---|
| Stop, keep data | `pwsh infra/kb_e2e.ps1 down` |
| Stop, **wipe** data | `pwsh infra/kb_e2e.ps1 down -v` |
| Tail logs | `pwsh infra/kb_e2e.ps1 logs -f` |
| Service status | `pwsh infra/kb_e2e.ps1 ps` |
| Rebuild one service | `pwsh infra/kb_e2e.ps1 up -d --build --no-deps kb-backend` |

> **Fresh volume note:** `docker-entrypoint-initdb.d` scripts run only on a new Postgres
> volume. `docker-compose.yml` mounts `init.sql` (KB tables); the overlay adds `seed.sql`
> (dev tenant / roles / knowledge base). On an existing volume the data is already there
> — init is skipped. Wipe with `down -v` and bring up again to re-seed.

> **First start with local models:** if `REMOTE_EMBED_URL` / `REMOTE_RERANK_URL` are not
> set, `model-service` downloads weights into `infra/model-service/models/` (~2.3 GB,
> persisted across restarts). Both remote URLs set → download skipped, starts in seconds.

### Port map

| Port | Service |
|---|---|
| `3000` | Vite dev server (frontend) |
| `8000` | Kong proxy (all API traffic goes here) |
| `8050` | kb-backend direct (fail-closed — returns 401 without Kong token) |
| `8080` | Keycloak |
| `8600` | model-service |
| `8900` | Kong admin API (debug only — not on host in production) |
| `5432` | PostgreSQL |
| `27017` | MongoDB |
| `6333` | Qdrant |
| `7687` | Neo4j bolt |
| `9000` / `9001` | MinIO API / console |
| `9092` | Kafka SASL (external clients) |

---

## Full platform stack

The base [`docker-compose.yml`](./docker-compose.yml) defines all platform services.
Run from the repo root with a `.env` file (`Copy-Item .env.example .env`).

```powershell
# local dev
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --build
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml down   # -v to wipe
# shared dev server
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d --build
# validate config without starting
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml config
# test fixture (isolated Postgres on :5433)
docker compose -f infra/docker-compose.test.yml up -d
```

---

## Switching the model backend (local CPU ↔ remote GPU)

`model-service` is the single model gateway. All callers (ingestion worker, LightRAG
pipeline) hit only its three endpoints and never know which backend is active:

| Endpoint | Used for | Callers |
|---|---|---|
| `POST /embed` | text → vector | LightRAG query-embed, reembed, gold/silver ingestion |
| `POST /rerank` | rerank candidates | LightRAG rerank step |
| `POST /generate` | LLM text / JSON | LightRAG `canonicalize`, ingestion `llm_client` |

For each capability the active backend is the first registered one that is `available()`.
A **remote** backend (Colab GPU behind a cloudflared tunnel) is available when its
`REMOTE_*_URL` env is set; otherwise the **local** CPU model serves. Switching is a
single env change — no code, no image rebuild.

| env (on `model-service`) | effect |
|---|---|
| `REMOTE_EMBED_URL` | set → `/embed` forwards there; empty → local CPU (`EMBED_MODEL`) |
| `REMOTE_RERANK_URL` | set → `/rerank` forwards there; empty → local CPU reranker |
| `REMOTE_LLM_URL` | set → `/generate` forwards to this OpenAI-compatible server; empty → 503 |
| `REMOTE_API_KEY` / `REMOTE_LLM_KEY` | bearer keys for the remote embed/rerank and LLM servers |

> When both `REMOTE_EMBED_URL` and `REMOTE_RERANK_URL` are set, model-service skips
> downloading local weights entirely and starts in seconds. When either is unset the
> local model loads as fallback (~2.3 GB on first start).

Switch to a remote GPU tunnel without restarting the whole stack:

```powershell
# Set new tunnel URLs in your env file, then recreate just model-service:
$env:REMOTE_EMBED_URL = "https://<embed-tunnel>.trycloudflare.com"
$env:REMOTE_API_KEY   = "kb-emb-xxxx"
$env:REMOTE_LLM_URL   = "https://<vllm-tunnel>.trycloudflare.com"
$env:REMOTE_LLM_KEY   = "kb-IT-xxxx"
pwsh infra/kb_e2e.ps1 up -d --no-deps model-service
```

To revert to local CPU, clear those vars and recreate `model-service`. The Colab GPU
notebooks live in `model/embedding/embed_rerank_colab.ipynb` (embed + rerank) and
`model/vllm/vllm_qwen_colab.ipynb` (LLM) — each prints its tunnel URL + API key.

> **⚠️ The embed dim must match the index.** Switching **LLM** or **rerank** is safe
> any time. Switching **embed** to a different dimension requires re-embedding the whole
> corpus first (`reembed.py`) — otherwise every search fails with a dim mismatch.

---

## Layout

```
infra/
  docker-compose.yml                     # main platform base (all services)
  docker-compose.kb_e2e.yml              # KB overlay (extends docker-compose.yml)
  docker-compose.kb_and_authen.yml       # standalone KB + auth stack
  docker-compose.test.yml                # isolated Postgres test fixture
  kb_e2e.ps1                             # helper: two-file overlay bring-up
  kb_and_authen.ps1                      # helper: standalone KB stack
  .env.kb-testing.example                # copy → .env.kb-testing, fill in tunnels
  Dockerfile.kb                          # kb-backend image
  Dockerfile.dev                         # dev-only containerised frontend
  model-service/                         # model gateway (embed · rerank · generate)
  kb-ingestion-worker/                   # bronze→silver→gold ingestion worker
  kong/          kong-setup.sh           # Kong custom plugin + route setup script
  keycloak/      realm-export.json       # Keycloak realm definition
  Postgres/      init.sql  seed.sql      # KB schema + dev seed data
  Neo4j/                                 # Neo4j custom image (APOC plugins)
  kafka/         kafka-broker-jaas.config.example
  openbao/       haproxy/  litellm/
  sql/           create-extra-dbs.sql  init.sql  release-init.sql
  Qdrant/        COLLECTIONS.md          # Qdrant collection schema reference
```
