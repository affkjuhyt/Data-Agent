# Security Posture & Release Hardening — GTEL Data Agent

This documents the security controls in place and the hardening items tracked toward a
production release. Operational procedures are in [`RUNBOOK.md`](./RUNBOOK.md).

---

## 1. Authentication & authorization

- **Fail-closed gateway.** All API traffic must pass through **Kong** carrying a verified
  Keycloak identity (`X-Kong-Verified`, realm `aeroflow`, `aeroflow-jwks` plugin). Any
  request without it gets `401`. Services never trust a raw bearer token directly —
  identity is injected by Kong.
- **Multi-tenant isolation via Postgres RLS.** Tenant tables carry `tenant_id` with
  row-level-security policies; child tables without their own `tenant_id` are covered by
  FK-chain `EXISTS` policies (`enable_tenant_rls` + `rls_kb_child_tables` migrations).
  Isolation is asserted by an e2e test (`e2e-tenant-isolation` CI job).
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

| Runs as non-root (`USER app`) | Intentionally root — documented |
|---|---|
| kb-backend, kb-ingestion-worker, workflow-runtime, flow-builder, orchestrator, auth-api, model-service, minio-service, release-worker, audit-bridge, audit-consumer, memory-consumer, jwks-refresher, schema-crawler-worker | **sandbox-executor** — spawns sandbox containers via the mounted Docker socket; a non-root user would need the host docker group (root-equivalent). Proper fix: rootless Docker / gVisor. |

`infra/Dockerfile`, `infra/Dockerfile.frontend` (both **LEGACY**, not in the supported
entrypoint set) and `infra/Dockerfile.dev` (dev-only) are out of scope for the prod
non-root requirement.

Notes on the two special cases:
- **model-service** downloads model weights at runtime into `/app/models`, which is
  `chown`ed to `app` so the mounted volume stays writable.
- **kb-ingestion-worker** installs Chromium to a world-readable `PLAYWRIGHT_BROWSERS_PATH`
  (`/ms-playwright`) so the non-root user can still launch the browser; the app user has a
  writable `$HOME` for Chromium's profile/cache.

### Base-image digest pinning — TODO (tracked)

Base images are currently pinned by **tag** (e.g. `python:3.11-slim`), not by digest.
Digest pinning must use the **multi-arch manifest-list digest**, not a locally-resolved
platform digest — pinning an arm64 image digest would break amd64 CI/prod builds. Resolve
in CI (buildx / `docker buildx imagetools inspect`) so it stays arch-correct:

```bash
docker buildx imagetools inspect python:3.11-slim --format '{{.Manifest.Digest}}'
# FROM python:3.11-slim@sha256:<manifest-list-digest>
```

`services/workflow-runtime/Dockerfile` carries the reference `TODO(production)` comment
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
- **ruff** (incl. `BLE` blind-except, `ASYNC` blocking-call lints) is blocking.
- **Coverage gate** (`fail_under` in `pyproject.toml`) fails the test job on a coverage
  collapse; ratchet upward (35 → 50 → 70) after each measured CI run.
- **Strict typing** (`mypy --disallow-untyped-defs --disallow-any-generics`): the two
  innermost, most-reused layers — `libs/basemodel` + `libs/shared` — are kept
  **strict-clean and enforced BLOCKING** in CI ("Strict typecheck · core libs"). The
  rest of the tree is report-only. Burn-down backlog (strict-error counts, expand the
  `pyproject` override + a blocking invocation per layer as each reaches zero):

  | Layer | strict errors | note |
  | --- | --- | --- |
  | `libs/basemodel` | **0** ✅ | enforced |
  | `libs/shared` | **0** ✅ | enforced |
  | `libs/database_connector` | **0** ✅ | enforced — all 3 lib layers now strict-clean |
  | `services/orchestrator` | ~100 | next |
  | `services/kb_backend` | ~337 | largest surface |

  > Only globs matching real importable packages work in the `pyproject` override;
  > hyphenated service dirs (`services/auth-api`, …) must be enforced by invoking mypy
  > on the directory directly in CI.

## 6. Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainers rather than opening a public
issue. Do not include exploit details or affected credentials in public trackers.
