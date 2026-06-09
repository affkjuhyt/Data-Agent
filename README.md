# GTEL Data Agent

Multi-tenant AI agent platform with workflow runtime, flow builder, auth and gateway services, storage layers, and observability tooling.

## Docker First

The active Docker entrypoints live under [docker/](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker>).

For the current refactored stack summary, see [docs/current-docker-stack.md](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docs/current-docker-stack.md>).

Use these files as the current source of truth:

- [docker/docker-compose.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.yml>): aggregate entrypoint that includes the stack layers
- [docker/docker-compose.storage.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.storage.yml>): storage layer
- [docker/docker-compose.security.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.security.yml>): auth, gateway, and secret-management layer
- [docker/docker-compose.messaging.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.messaging.yml>): messaging layer
- [docker/docker-compose.observability.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.observability.yml>): observability and AI-support layer
- [docker/docker-compose.app.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.app.yml>): application and frontend layer
- [docker/docker-compose.local.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.local.yml>): local developer override
- [docker/docker-compose.dev.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.dev.yml>): shared dev server override
- [docker/docker-compose.test.yml](</C:/Users/Admin/Documents/Data Agent/Data-Agent/docker/docker-compose.test.yml>): test-only PostgreSQL fixture

This repository still contains older Docker artifacts and legacy docs. For now, do not use root-level `docker compose up` commands that omit the `docker/` paths.

## Prerequisites

- Docker Desktop with Docker Compose v2
- A local [`.env`](</C:/Users/Admin/Documents/Data Agent/Data-Agent/.env>) created from [`.env.example`](</C:/Users/Admin/Documents/Data Agent/Data-Agent/.env.example>)

Bootstrap once:

```powershell
Copy-Item .env.example .env
```

Then fill in the required values in `.env`, especially:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `KAFKA_ADMIN_PASSWORD`
- `AUDIT_BRIDGE_KAFKA_PASSWORD`
- `AUDIT_CONSUMER_KAFKA_PASSWORD`
- `RELEASE_WORKER_KAFKA_PASSWORD`

## Official Docker Commands

### Local machine

Start or refresh the full local stack:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml up -d --build
```

Stop the local stack and keep data:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml down
```

Stop the local stack and remove volumes:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml down -v
```

### Shared dev server

Start the shared dev shape:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d --build
```

Stop the shared dev shape:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml down
```

### Compose validation

Check the resolved local config:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml config
```

Check the resolved shared-dev config:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml config
```

### Test fixture

Start the isolated PostgreSQL fixture used by connector and Alembic tests:

```powershell
docker compose -f docker/docker-compose.test.yml up -d
```

Stop the test fixture:

```powershell
docker compose -f docker/docker-compose.test.yml down -v
```

## Chạy trên VM / Dev Server (Linux)

### Shell alias (chạy 1 lần)

```bash
source ~/.bashrc   # nếu chưa load alias dc
# hoặc thêm thủ công vào ~/.bashrc:
alias dc='docker compose -f ~/Data-Agent/docker/docker-compose.yml \
                         -f ~/Data-Agent/docker/docker-compose.local.yml \
                         -f ~/Data-Agent/docker/docker-compose.llm.yml'
```

---

### Khởi động llama-server (local LLM)

llama-server chạy trực tiếp trên host (không qua Docker), cần bật **trước** khi start stack:

```bash
# Tải model lần đầu (chỉ chạy 1 lần)
bash scripts/download-model.sh

# Khởi động llama-server ở port 8088
bash scripts/start-llama.sh &

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

---

## Runtime Shape

The current base stack includes these service groups:

- Storage: PostgreSQL, MongoDB, Redis, MinIO
- Search and graph: Qdrant, Neo4j
- Security and gateway: Keycloak, Kong, OpenBao
- Messaging: Kafka
- Observability and AI tooling: LiteLLM, Langfuse, Jaeger, Elasticsearch
- Application services: workflow-runtime, flow-builder, kb-backend, auth-api, minio-service, release-worker, audit-bridge, audit-consumer, jwks-refresher
- Frontend: Vite dev server on `5173`

The local and shared-dev overrides both add:

- `migrate`
- `seed`

Separate intent-specific Docker paths also exist:

- Test-only: `docker/docker-compose.test.yml`
- Standalone model experiment: `model/ollama/docker/docker-compose.yml`
- Legacy archive: `archive/docker-legacy/`

## Common URLs

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Kong proxy | http://localhost:8000 |
| Kong admin | http://localhost:8900 |
| workflow-runtime | http://localhost:8001 |
| flow-builder | http://localhost:8002 |
| kb-backend | http://localhost:8050 |
| auth-api | http://localhost:8200 |
| minio-service | http://localhost:8400 |
| Keycloak | http://localhost:8080 |
| Langfuse | http://localhost:3001 |
| LiteLLM | http://localhost:4000 |
| MinIO console | http://localhost:9001 |
| Jaeger | http://localhost:16686 |

## Notes

- `docker compose config` requires a real [`.env`](</C:/Users/Admin/Documents/Data Agent/Data-Agent/.env>) because several services reference `env_file: ../.env`.
- `docker/docker-compose.yml` is now an aggregate compose entrypoint that includes the layer-specific stack files under `docker/`.
- `docker/docker-compose.test.yml` is the supported test fixture for the refactored Docker stack.
- Renaming the compose files to `compose*.yml` is deferred to a later step so we can first align docs, scripts, and build contexts safely.
