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
| **kb_e2e** | [`docker-compose.yml`](./docker-compose.yml) + [`docker-compose.kb_e2e.yml`](./docker-compose.kb_e2e.yml) (hai file `-f`, xem Step 3) | **Overlay** on the main platform compose. Shares the same `dataagent` DB, Kafka internal listener (`kafka:9094`), and network as the full platform. Adds dev-friendly memory limits on Neo4j and KB tuning knobs. | **The standard way to bring up the stack** — day-to-day local dev, KB + ingestion + auth chain, pre-merge validation. |
| **Full platform** | [`docker-compose.yml`](./docker-compose.yml) + an override | The whole Data Agent OS — every service group (storage, security, messaging, observability, all app services). | Running the entire platform, not just KB. |
| **Test fixture** | [`docker-compose.test.yml`](./docker-compose.test.yml) | Isolated PostgreSQL only. | Connector / ORM tests. |

> Dev stacks re-publish `kb-backend` on `127.0.0.1:8050` (loopback only). It returns `401`
> for a request with no identity headers, but it is **not** an auth boundary: identity is
> plain `X-Kong-Verified` / `X-Tenant-Id` / `X-User-Roles` headers that Kong injects after
> stripping client copies, so anything reaching this port can forge them and act as any
> tenant or role. Only Kong (`:8000`) is trusted — never publish `:8050` beyond loopback.

## Bring up only one feature — Compose profiles

`docker-compose.yml` tags each service with a **feature profile** so you can start just
the slice you are working on instead of the whole platform. Four services carry **no**
profile and therefore always start (the core auth + system-of-record):
`postgres`, `keycloak`, `kong-migration`, `kong`.

```bash
# Run from the repository root
docker compose -f deploy/docker/docker-compose.yml --profile authoring up -d   # design flows: + flow-builder, frontend, mongo, redis
docker compose -f deploy/docker/docker-compose.yml --profile kb        up -d   # Knowledge Base: + kb-backend, ingestion, model-service, litellm + qdrant/neo4j/minio/kafka/mongo
docker compose -f deploy/docker/docker-compose.yml --profile iam       up -d   # auth/governance: + auth-api, audit-bridge, audit-consumer, openbao, kafka
docker compose -f deploy/docker/docker-compose.yml --profile release   up -d   # release: + release-worker, minio-service + minio/mongo/kafka/redis
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.app.yml --profile runtime up -d  # agent runtime (full): + workflow-runtime, orchestrator, kb-backend, sandbox-executor, memory-consumer, schema-crawler-worker

# Combine profiles freely
docker compose -f deploy/docker/docker-compose.yml --profile kb --profile authoring up -d          # KB + its UI
docker compose -f deploy/docker/docker-compose.yml --profile kb --profile testdb up -d             # KB + a ClickHouse test DB for db_connector
docker compose -f deploy/docker/docker-compose.yml --profile runtime --profile observability up -d # runtime + Jaeger/Langfuse/ELK
```

| Profile | Adds (app) | Adds (stores) |
|---|---|---|
| `authoring` | flow-builder, frontend | mongo, redis |
| `kb` | kb-backend, kb-ingestion-worker, model-service, litellm, minio-service | qdrant, neo4j, minio, kafka, mongo, redis |
| `runtime` | workflow-runtime, orchestrator, kb-backend, model-service, litellm | mongo, redis, qdrant, neo4j, kafka, openbao |
| `iam` | auth-api, audit-bridge, audit-consumer | kafka, openbao |
| `release` | release-worker, minio-service | minio, mongo, kafka, redis |
| `observability` | langfuse (+ worker, minio-init) | elasticsearch, jaeger, clickhouse, minio, redis |
| `testdb` | — | clickhouse (throwaway DB target for `db_connector`) |
| `models` | ollama | — (local GPU model backend; dev usually points litellm at a host llama-server instead) |
| `legacy-jwt` | jwks-refresher | — (**only** if Kong runs the built-in `jwt` plugin instead of `aeroflow-jwks` — check `curl :8001/plugins`) |

> **Note — the full `runtime` lives across two files.** `sandbox-executor` (+ `sandbox-dockerd`),
> `memory-consumer`, and `schema-crawler-worker` are defined in
> [`docker-compose.app.yml`](./docker-compose.app.yml), which carries the same profiles. So the
> **complete** agent runtime (with the sandbox) is `-f docker-compose.yml -f docker-compose.app.yml
> --profile runtime`; the base file alone gives a runtime without the sandbox/memory/schema workers.
>
> When you enable a profile, every service tagged with it starts; a shared store (e.g. `kafka`)
> carries **all** the profiles that use it, so it comes up under any of them.

---

## Bring up the stack — kb_e2e

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
Copy-Item deploy/docker/.env.kb-testing.example deploy/docker/.env.kb-testing
```

Edit `deploy/docker/.env.kb-testing`:
- `REMOTE_LLM_URL` / `REMOTE_LLM_KEY` — Colab vLLM tunnel. Without it the gold
  ingestion stage and LightRAG canonicalize step return 503; bronze→silver and
  retrieval still work.
- `REMOTE_EMBED_URL` / `REMOTE_RERANK_URL` / `REMOTE_API_KEY` — remote GPU for
  embed/rerank. When set, model-service skips the local model download entirely
  and starts in seconds.
- `KAFKA_ADMIN_PASSWORD` — required (no compose default). Set to `dev123` for local
  dev. Must match `deploy/docker/kafka/kafka-broker-jaas.config`.

The file ships `KAFKA_ADMIN_PASSWORD=dev123` and `POSTGRES_PASSWORD=dev123` as safe
local defaults.

### Step 2 — create the Kafka JAAS config (first time only)

```powershell
Copy-Item deploy/docker/kafka/kafka-broker-jaas.config.example deploy/docker/kafka/kafka-broker-jaas.config
# Then substitute the passwords — for local dev, replace all REPLACE_WITH_* with dev123
```

### Step 3 — start the stack

```powershell
# Two-file overlay (KB-relevant services only):
docker compose `
  -f deploy/docker/docker-compose.yml `
  -f deploy/docker/docker-compose.kb_e2e.yml `
  --env-file deploy/docker/.env.kb-testing `
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

Open **http://localhost:3000** and log in via Keycloak as **`helios-admin` /
`H3lios@Admin01!`** (full perms) or **`helios-engineer` / `H3lios@Eng001!`** (limited
— write actions return `403`).

All six seeded dev users:

| Username | Password | Role |
|---|---|---|
| `helios-admin` | `H3lios@Admin01!` | `platform-admin` (all perms) |
| `helios-engineer` | `H3lios@Eng001!` | `ai-engineer` |
| `helios-viewer` | `H3lios@View001!` | `executive-viewer` |
| `platform-admin` | `Platform@Admin01!` | `platform-admin` |
| `ai-engineer` | `AiEng@001!` | `ai-engineer` |
| `executive-viewer` | `Exec@View001!` | `executive-viewer` |

All users share tenant `a0000000-0000-0000-0000-000000000001` (the seeded dev tenant).

> The Vite dev server is **not** part of the Docker stack — start it manually each
> session with `npm run dev`.

| Action | Command |
|---|---|
| Stop, keep data | `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml down` |
| Stop, **wipe** data | `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml down -v` |
| Tail logs | `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml logs -f` |
| Service status | `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml ps` |
| Rebuild one service | `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml up -d --build --no-deps kb-backend` |

> **Fresh volume note:** `docker-entrypoint-initdb.d` scripts run only on a new Postgres
> volume. `docker-compose.yml` mounts `init.sql` (KB tables); the overlay adds `seed.sql`
> (dev tenant / roles / knowledge base). On an existing volume the data is already there
> — init is skipped. Wipe with `down -v` and bring up again to re-seed.

> **First start with local models:** if `REMOTE_EMBED_URL` / `REMOTE_RERANK_URL` are not
> set, `model-service` downloads weights into `deploy/docker/model-service/models/` (~2.3 GB,
> persisted across restarts). Both remote URLs set → download skipped, starts in seconds.

### Port map

| Port | Service |
|---|---|
| `3000` | Vite dev server (frontend) |
| `8000` | Kong proxy (all API traffic goes here) |
| `8050` | kb-backend direct, loopback only (debug — NOT an auth boundary, see note above) |
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
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.local.yml up -d --build
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.local.yml down   # -v to wipe
# shared dev server
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.dev.yml up -d --build
# validate config without starting
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.local.yml config
# test fixture (isolated Postgres on :5433)
docker compose -f deploy/docker/docker-compose.test.yml up -d
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
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml up -d --no-deps model-service
```

To revert to local CPU, clear those vars and recreate `model-service`. The Colab GPU
notebooks live in `model/embedding/embed_rerank_colab.ipynb` (embed + rerank) and
`model/vllm/vllm_qwen_colab.ipynb` (LLM) — each prints its tunnel URL + API key.

> **⚠️ The embed dim must match the index.** Switching **LLM** or **rerank** is safe
> any time. Switching **embed** to a different dimension requires re-embedding the whole
> corpus first (`reembed.py`) — otherwise every search fails with a dim mismatch.

---

## Troubleshooting

### `kb-ingestion-worker` crash-loops on start / `model-service /embed` → 500

Worker log shows `httpx.HTTPStatusError: Server error '500' for .../embed` and the
container restarts every ~60 s; `model-service` log shows
`URLError: <urlopen error [Errno -2] Name or service not known>`.

**Cause:** a stale Colab tunnel. `trycloudflare.com` URLs change every time the
notebook restarts, so the `REMOTE_*` values in `deploy/docker/.env.kb-testing` no longer
resolve. `model-service /health` stays green (it only checks the LLM), which hides
the broken embed path; the worker's startup embed probe is what fails.

**Fix** — put the new tunnel URLs + keys in `deploy/docker/.env.kb-testing`, then recreate
the two services that read them. The worker **must** be recreated too (its startup
probe is bound to the old URL):

```powershell
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.kb_e2e.yml `
  --env-file deploy/docker/.env.kb-testing `
  up -d --no-deps --force-recreate model-service kb-ingestion-worker
```

Verify embed is live (should return vectors, not `Internal Server Error`):

```powershell
curl -X POST http://localhost:8600/embed -H "Content-Type: application/json" -d '{\"texts\":[\"probe\"]}'
```

### Login fails: `helios-admin` → `invalid_grant / "Invalid user credentials"`

The realm import ships **no** passwords — credentials live in the Keycloak volume and
can drift (a stray `otp` credential gets enrolled, so the password grant is rejected).
`helios-engineer` usually still works, which confirms the chain itself is fine.

**Fix** — using the master admin (`admin` / `admin` on realm `master`), delete the
stray `otp` credential on the user and reset the password to `H3lios@Admin01!`
(non-temporary) via the Keycloak admin API
(`/admin/realms/aeroflow/users/{id}/credentials` and `.../reset-password`).

---

## Layout

```
deploy/docker/
  docker-compose.yml                     # main platform base (all services)
  docker-compose.kb_e2e.yml              # KB overlay (extends docker-compose.yml)
  docker-compose.test.yml                # isolated Postgres test fixture
  .env.kb-testing.example                # copy → .env.kb-testing, fill in tunnels
  Dockerfile.kb                          # kb-backend image
  Dockerfile.dev                         # dev-only containerised frontend
  model-service/                         # model gateway (embed · rerank · generate)
  kb-ingestion-worker/                   # bronze→silver→gold ingestion worker
  kong/          kong-setup.sh           # Kong custom plugin + route setup script
  keycloak/      realm-export.json       # Keycloak realm definition
  Neo4j/                                 # Neo4j custom image (APOC plugins)
  kafka/         kafka-broker-jaas.config.example
  openbao/       haproxy/  litellm/
  sql/           create-extra-dbs.sql  init.sql  release-init.sql  kb-seed.sql
  Qdrant/        COLLECTIONS.md          # Qdrant collection schema reference
```
