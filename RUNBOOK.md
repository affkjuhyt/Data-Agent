# Operations Runbook — GTEL Data Agent

Operational procedures for bringing up, migrating, verifying, and troubleshooting the
stack. For architecture see [`README.md`](./README.md); for security posture see
[`SECURITY.md`](./SECURITY.md).

---

## 1. Prerequisites

- Docker Engine / OrbStack with Compose v2 (`docker compose`, not `docker-compose`).
- A populated `.env` — copy the template and fill secrets:
  ```bash
  cp .env.example .env
  ```
  `.env` is **git-ignored** and must never be committed. Every `*_PASSWORD` /
  `*_API_KEY` / `*_SECRET` needs a real value; the prod overlay must not rely on
  template defaults.
- Node 20 + `npm ci` for the frontend (`npm run dev`).

## 2. Bring the stack up

Compose files live in `infra/` and are composed as a base + overlays. Pick the stack
that matches the task (see the table in `README.md`):

```bash
# Standard local dev — KB data plane + ingestion + Keycloak + Kong
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml up -d --build

# Seed Kong routes + the aeroflow-jwks plugin (required once per fresh stack)
bash infra/kong/kong-setup.sh
```

The KB backend is **fail-closed**: requests must arrive through Kong with a verified
Keycloak identity (`X-Kong-Verified`) or get `401`. Hitting `kb-backend` directly on
`:8050` returning `401` is expected (confirms fail-closed).

## 3. Database migrations (Alembic)

Migrations run through **asyncpg** (same driver as production migrate).

```bash
# Apply all migrations to head
alembic upgrade head

# Inspect state
alembic current
alembic history

# Roll back the last migration
alembic downgrade -1
```

- RLS is enforced via `enable_tenant_rls` + `rls_kb_child_tables` (FK-chain EXISTS
  policies for child tables without their own `tenant_id`). Never disable RLS on a
  tenant table to "fix" a query — scope the query by tenant instead.
- The migration chain is covered by `tests/integration/test_alembic_migrations.py`.

## 4. Health verification

Every service exposes `GET /health`. After bring-up:

```bash
# Gateway reachable + Keycloak realm published
curl -sf http://localhost:8000/ >/dev/null && echo "kong OK"
curl -sf http://localhost:8080/realms/aeroflow/.well-known/openid-configuration >/dev/null && echo "keycloak OK"

# Per-service (adjust ports): auth-api 8200, model-service 8001, flow-builder 8002,
# orchestrator 8003, release-worker 8100, minio-service 8400, sandbox-executor 8500
curl -sf http://localhost:8200/health && echo " auth-api OK"
```

Container-level health is also wired via `HEALTHCHECK` in the compose stack —
`docker compose ps` shows `healthy`/`unhealthy`.

## 5. LLM / model backend

`model-service` (`:8001`) is the embed/rerank/generate gateway. Generation is proxied
to an LLM backend via `REMOTE_LLM_URL`.

- **Native Ollama (recommended on Apple Silicon — Metal GPU):** run `ollama serve` on
  the host; containers reach it at `http://host.docker.internal:11434`. Existing models
  `qwen2.5vl:7b` / `qwen2.5:7b` are used (set `REMOTE_LLM_MODEL`).
- Do **not** run Ollama inside Docker on macOS — it falls back to CPU and pegs the VM.
- Model weights are **not** baked into the `model-service` image; they download at first
  start into the mounted `HF_HOME` volume (`infra/model-service/models`).

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker ps` hangs for a long time | A container pegging the Docker VM (seen with Ollama-in-Docker on CPU). Find/stop it: `docker stats --no-stream`; prefer native Ollama. |
| API returns `401` everywhere | Kong not seeded — run `bash infra/kong/kong-setup.sh`; or Keycloak realm not up yet. |
| `Dockerfile cannot be empty` | A Dockerfile got truncated by a bad edit — restore with `git checkout HEAD -- <path>`. |
| Build re-downloads torch/docling every time | Cache invalidated (e.g. Dockerfile/base changed). The pip layer for `kb-ingestion-worker` / `model-service` installs CPU-only torch first — expect ~minutes on a cold cache. |
| `ModuleNotFoundError: shared` in a service | That service copies `libs/shared` as the bare `shared` package (build context = repo root). Confirm compose `context: ..` + `COPY libs/shared/ ./shared/`. |
| Retrieval quality poor | Check the LLM backend is actually reachable (`REMOTE_LLM_URL`) and the right model is set — a degraded/CPU model is the usual root cause, not the pipeline. |

## 7. Pre-release checklist

Before promoting a build (see `SECURITY.md` for the security items):

- [ ] `ruff check .` and `ruff format --check .` clean
- [ ] `lint-imports` → contracts kept (architecture rule)
- [ ] `pytest -m "unit or integration"` green; coverage ≥ gate (`fail_under` in `pyproject.toml`)
- [ ] `alembic upgrade head` applies cleanly on a fresh DB
- [ ] `docker compose ... config` validates for each target overlay
- [ ] CI `security-scan` reviewed (pip-audit + trivy) — no unexplained HIGH/CRITICAL
- [ ] `.env` for the target environment has real secrets (no template defaults)
