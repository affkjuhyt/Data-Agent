# Security Posture & Release Hardening — GTEL Data Agent

This documents the security controls in place and the hardening items tracked toward a
production release. Operational procedures are in [`RUNBOOK.md`](./RUNBOOK.md).

---

## 1. Authentication & authorization

- **The api verifies every caller itself.** Kong routes `/v1` to the api and its
  `aeroflow-jwks` plugin rejects a request without a valid Keycloak token (realm `aeroflow`,
  audience `dataagent-api`), but ADR-30 keeps the check in the api as well: it verifies the
  token against the realm's JWKS (signature, issuer, expiry, audience) on every request and
  reads nothing from identity headers (`backend/services/api/kernel/auth.py`). The
  organisation comes from the `org_id` claim; who the caller is and what they may do come
  from `org.app_user` and `org.role.capabilities`, resolved per request.
- **API clients use client-credentials tokens** (ADR-30, `integration.api_client`). The
  legacy `sk-` API keys, the per-tenant IP allowlist and the Secrets Vault of `auth_api` were
  removed with it on 01/11/2026; Kong now answers an `sk-` bearer with 401 like any other
  malformed token.
- **The agent tier has no external Service.** worker-agent, worker-ingest, worker-document and
  crawler take work from the database, Temporal and events, never from a caller; only Kong
  (and Keycloak, for the browser login redirect) is published outside the cluster.
- **Mutual TLS (optional).** `deploy/docker/certs/gen-certs.sh` issues the CA plus a
  `clientAuth` certificate for Kong and a `serverAuth` cert for the backend; kong-setup.sh
  attaches the client certificate to the api service when the files exist.
- **Audit.** The api appends one `audit.audit_log` row per `/v1` call, outcome included
  (`backend/services/api/kernel/access_log.py`, A1.8); the modules that must not act unaudited
  write their own row inside the action. The V2 `data.access` Kafka pipeline went with
  platform-workers.
- **Multi-tenant isolation via Postgres RLS.** Tenant tables carry `tenant_id` with
  row-level-security policies; child tables without their own `tenant_id` are covered by
  FK-chain `EXISTS` policies (`enable_tenant_rls` + `rls_kb_child_tables` migrations).
- **DB RBAC.** `runtime_writer` is scoped to `INSERT/SELECT` on the conversation tables
  only — least privilege for the runtime path.

## 2. Secrets

- `.env` is **git-ignored**; `.env.example` is the committed template with placeholder
  values only. Verified: no `.env` is tracked.
- Kafka SASL, Keycloak, and provider API keys come from the environment — never hardcoded.
- Credentials used at run time live in OpenBao, not in Postgres: a source's warehouse
  password is written by the api (write-only AppRole) and read by worker-agent, worker-ingest
  and crawler through their own read-only AppRoles; only the path is in the database (BR-14).
- CI runs a **secret scan** (trivy `secret` scanner) over the tree.
- **Action for prod:** rotate any credential that ever appeared in a template default;
  the prod overlay must drop all defaults.

## 3. Container hardening

### Non-root

Application images drop root and run as an unprivileged `app` user. Status after the
release-hardening pass:

| Runs as non-root (`USER` in the image) | Root in the image — documented |
|---|---|
| api, worker-agent, worker-ingest, worker-document, crawler, migrate | — (none since sandbox-executor was removed) |

`frontend/Dockerfile.dev` (dev-only) is out of scope for the prod non-root requirement.

Notes on special cases:
- **crawler** fetches without a browser (no Chromium in any image since the web-crawler and
  kb-worker were removed); it refuses internal names and private addresses (docs/RUNBOOK.md §10d).

### Base-image digest pinning — TODO (tracked)

Base images are currently pinned by **tag** (e.g. `python:3.11-slim`), not by digest.
Digest pinning must use the **multi-arch manifest-list digest**, not a locally-resolved
platform digest — pinning an arm64 image digest would break amd64 CI/prod builds. Resolve
in CI (buildx / `docker buildx imagetools inspect`) so it stays arch-correct:

```bash
docker buildx imagetools inspect python:3.11-slim --format '{{.Manifest.Digest}}'
# FROM python:3.11-slim@sha256:<manifest-list-digest>
```


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
  rule (`libs.contracts` and `libs.shared` are leaves, the api kernel depends on no module,
  worker-agent and crawler import neither the api nor each other).
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
  | `backend/libs/shared` | **0**, enforced |
  | `backend/services/{api,worker_agent,crawler}` (`--disallow-untyped-defs --disallow-any-generics`) | CI blocking step (`make type-v3`) |

  Not on the strict list: `backend/libs/{semantic,indicator}`. The whole-tree run is report-only (`|| true`).

## 6. Reporting a vulnerability

Report suspected vulnerabilities privately to the maintainers rather than opening a public
issue. Do not include exploit details or affected credentials in public trackers.
