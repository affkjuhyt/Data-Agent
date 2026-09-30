# Security Posture & Release Hardening — GTEL Data Agent

This documents the security controls in place and the hardening items tracked toward a
production release. Operational procedures are in [`RUNBOOK.md`](./RUNBOOK.md).

---

## 1. Authentication & authorization

- **Fail-closed gateway.** All API traffic must pass through **Kong** carrying a verified
  Keycloak identity (`X-Kong-Verified`, realm `aeroflow`, `aeroflow-jwks` plugin). Any
  request without it gets `401`. Services never trust a raw bearer token directly —
  identity is injected by Kong.
- **The agent tier is behind the gateway too.** The orchestrator runs inside
  `workflow-runtime` (`/api/orchestrate/*`), which is a ClusterIP Service on `8240`
  in the production set; Kong's workflow_runtime route is the only way in, and the
  service enforces the shared caller check instead of reading raw headers.
  Previously nothing verified the caller and the orchestrator fell back to
  `DEV_TENANT_ID`, so any caller reaching its port could act as any tenant. The
  Vite dev proxy, which used to inject a hardcoded `platform-admin` identity, now
  routes through Kong like everything else. (The dev set publishes every Service
  as a NodePort for debugging; the caller check still applies there.)
- **Gateway proof (`X-Gateway-Key`).** `X-Kong-Verified` is only a header: a caller that
  reaches a service port directly can send it and be believed. The plugin now also injects
  a shared secret (`gateway_key` ↔ `GATEWAY_KEY`) that every upstream compares in constant
  time, so the marker means "Kong verified a JWT", not "someone typed this". Leaving
  `GATEWAY_KEY` unset keeps the old, weaker behaviour and logs a warning on every
  request — set it everywhere except a laptop. (`KB_GATEWAY_KEY` is still read as a
  deprecated alias.)
- **Service-to-service identity.** `workflow-runtime` calls `kb-backend` directly
  (`kb_search` → LightRAG retrieve), so it cannot present a user's Keycloak token. It signs
  an **HMAC assertion** (`backend/libs/shared/service_identity.py`) carrying the real caller's
  tenant, user and roles, with a 120 s expiry; `kb-backend` verifies it and takes identity
  **only** from the token, ignoring accompanying `X-User-*` headers. Services permitted to
  assert an identity are allowlisted per service: `KB_ALLOWED_SERVICES`
  (kb-backend ← `workflow_runtime`) and `RUNTIME_ALLOWED_SERVICES` (empty: the
  orchestrator is in-process now, so nothing calls workflow-runtime with an
  assertion).
  The trust decision itself lives once in `backend/libs/shared/auth.resolve_caller`, so
  every service makes it identically.
  - Previously this call forged `X-Kong-Verified: true` plus a fixed
    `platform-admin,tenant-admin,kb-admin,engineer` role set, which made the document-level
    role filter (`kb_backend/operations/access.py`) inoperative for all agent traffic.
  - With no caller roles, or no `SERVICE_AUTH_SECRET`, retrieval now **fails closed** and
    returns no chunks rather than defaulting to any identity.
- **Mutual TLS (optional).** `deploy/docker/certs/gen-certs.sh` issues the CA plus `clientAuth`
  certificates for Kong *and* `workflow-runtime`, and a `serverAuth` cert for the backend.
  Setting `MTLS_CLIENT_CERT` / `MTLS_CLIENT_KEY` / `MTLS_CA_CERT` authenticates the
  connection; the signed assertion authenticates the request. They are independent layers —
  mTLS alone would let any CA-signed client assert any identity, and the assertion alone is
  only as strong as the network.
- **Data-access audit trail.** Every query an agent runs emits a `data.access`
  event (`backend/libs/shared/audit.py`) onto the existing Kafka pipeline; the `platform-workers` audit worker
  writes it to `DataAccessLog` — who, which roles, which connection, which tables,
  row count, latency, and the allow/deny/error outcome. Previously the audit
  covered **authentication events only**, so "who read this data" had no answer and
  the SQL lived only in Langfuse traces, which sample and expire.
  - **Statements are redacted, never stored raw.** A `WHERE` clause routinely holds
    exactly the personal data the audit protects, so literals become `?` and a
    SHA-256 of the original is kept alongside — enough to correlate repeat queries
    and prove which statement ran, without reproducing its arguments.
  - **Every outcome is recorded, not just successes.** A guard rejection is logged
    as `deny`, which is what makes "how often does the agent attempt a write?"
    answerable.
  - **Auditing records access, it does not gate it.** Emission is fire-and-forget
    and can never fail a user's turn. A control that must *block* on an unavailable
    audit log belongs at the policy layer.
  - **Action for prod — append-only is NOT yet enforced.** Nothing updates or
    deletes rows, but `app_user` still holds `UPDATE`/`DELETE` from the blanket
    grant in `enable_tenant_rls`. Tamper-evidence needs an INSERT-only writer role
    for the consumer plus revoking `UPDATE`/`DELETE` from everything else.
  - **Action for prod — retention.** The topic is provisioned at 90 days
    (`deploy/k8s/<env>/job/kafka-topics.yaml`); align it and the table's retention
    with the policy covering the data being queried.
- **Multi-tenant isolation via Postgres RLS.** Tenant tables carry `tenant_id` with
  row-level-security policies; child tables without their own `tenant_id` are covered by
  FK-chain `EXISTS` policies (`enable_tenant_rls` + `rls_kb_child_tables` migrations).
  Isolation is asserted by the e2e test `tests/integration/test_tenant_isolation.py`,
  run by `make e2e` against the dev cluster (not in CI), and in-process by the
  RLS integration tests (`tests/integration/auth_api/test_rls_enforcement.py`).
- **DB RBAC.** `runtime_writer` is scoped to `INSERT/SELECT` on the conversation tables
  only — least privilege for the runtime path.

## 2. Secrets

- `.env` is **git-ignored**; `.env.example` is the committed template with placeholder
  values only. Verified: no `.env` is tracked.
- Kafka SASL, Keycloak, and provider API keys come from the environment — never hardcoded.
- CI runs a **secret scan** (trivy `secret` scanner) over the tree.
- **Action for prod:** rotate any credential that ever appeared in a template default;
  the prod overlay must drop all defaults.

## 3. Container hardening

### Non-root

Application images drop root and run as an unprivileged `app` user. Status after the
release-hardening pass:

| Runs as non-root (`USER` in the image) | Root in the image — documented |
|---|---|
| kb-backend, kb-promote-silver/gold, kb-structured-data, kb-conflict-resolution, workflow-runtime (incl. the orchestrator), flow-builder, auth-api, model-service, llm-exporter, platform-workers (memory / schema-crawler / audit / web-crawler) | **sandbox-executor** — the image stays root for `SANDBOX_BACKEND=docker`, which spawns containers through the host Docker socket. On k8s it runs `SANDBOX_BACKEND=kubernetes` (runner pods in `dataagent-sandbox` via its ServiceAccount), and the production manifest sets `runAsNonRoot` / `runAsUser: 10001`. |

`frontend/Dockerfile.dev` (dev-only) is out of scope for the prod non-root requirement.

Notes on special cases:
- **model-service** downloads no weights: it runs no local model and proxies embed,
  rerank and generate to the GPU services (`REMOTE_*_URL`).
- **kb-worker** (the KB workers' image) and **platform-workers** install Chromium to a world-readable
  `PLAYWRIGHT_BROWSERS_PATH` (`/ms-playwright`) so the non-root user can still launch the
  browser; the app user has a writable `$HOME` for Chromium's profile/cache.

### Base-image digest pinning — TODO (tracked)

Base images are currently pinned by **tag** (e.g. `python:3.11-slim`), not by digest.
Digest pinning must use the **multi-arch manifest-list digest**, not a locally-resolved
platform digest — pinning an arm64 image digest would break amd64 CI/prod builds. Resolve
in CI (buildx / `docker buildx imagetools inspect`) so it stays arch-correct:

```bash
docker buildx imagetools inspect python:3.11-slim --format '{{.Manifest.Digest}}'
# FROM python:3.11-slim@sha256:<manifest-list-digest>
```

`backend/services/workflow_runtime/Dockerfile` carries the reference `TODO(production)` comment
for the pattern.

## 4. Supply-chain / CI security gates

The `security-scan` CI job (`.github/workflows/ci.yml`) runs on every PR:

- **pip-audit** over every `requirements*.txt` — known-CVE dependency detection.
- **trivy** filesystem scan — `vuln,secret,misconfig` at `HIGH,CRITICAL`.

Both are **report-only** today (matching the repo's existing report-then-ratchet gates)
with an existing baseline to burn down. Ratchet plan:
1. Triage the current findings; patch or document each.
2. Flip pip-audit to blocking per requirements file as it is cleared.
3. Set trivy `exit-code: "1"` once the HIGH/CRITICAL baseline is clean.

## 5. Architecture & code integrity gates

- **import-linter** (`lint-imports`) is **blocking** — enforces the layered dependency
  rule (`libs.basemodel` innermost; no `libs.database_connector` leakage).
- **ruff** (incl. `BLE` blind-except, `ASYNC` blocking-call lints) runs in CI
  (`python-lint`) but does **not** gate anything yet: the tree has ~1,500 findings and was
  never formatted, so the job is red on `main`. Until the repo-wide cleanup lands, a change
  is judged by "no new findings in the files it touches" (`CLAUDE.md` §1).
- **Coverage gate**: `fail_under = 48` in `pyproject.toml`, applied to the combined unit
  report (CI `python-coverage` job); it only ratchets up (target 50 → 70).
- **Strict typing** (`mypy --disallow-untyped-defs --disallow-any-generics`) is
  **BLOCKING** in CI ("Strict typecheck · core libs + services") on exactly these surfaces,
  each in its own mypy invocation (services aren't importable packages and share top-level
  basenames like `config.py`/`app.py`, which collide when checked together); `set -e` fails
  the job on the first surface that regresses:

  | Surface | strict errors |
  | --- | --- |
  | `backend/libs/{basemodel,database_connector,shared}` | **0**, enforced |
  | `backend/services/{kb_backend,workflow_runtime}` (incl. `workflow_runtime/orchestrator`) | **0**, enforced |
  | `backend/services/{auth_api,flow_builder,model_service,sandbox_executor,platform_workers}` | **0**, enforced |

  Not on the strict list: `backend/libs/{ingestion,semantic,indicator}` and the services
  `kb_backend/workers/*` (excluded from the `kb_backend` strict entry in ci.yml). The whole-tree run is report-only (`|| true`).

## 6. Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainers rather than opening a public
issue. Do not include exploit details or affected credentials in public trackers.
