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

## Frontend Development

The Knowledge Base UI is a Vite dev server. Its data layer (`src/lib/mockApi.ts`) is a thin fetch
wrapper around the **real KB FastAPI backend** — there is no standalone mock server on this branch.
Bring up the backend stack first (see [Full Stack — Docker](#full-stack--docker)), then:

```powershell
npm install
npm run dev           # Vite dev server on port 3000
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
