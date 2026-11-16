# Platform audit — Data Agent V3 (HEAD 25b5482e, 2026-11-12)

Read-only audit of API, governance, orchestration, observability, deployment, evaluation, tests, frontend wiring. Evidence as `path:line`. Verdicts: OK / PARTIAL / GAP.

## 1. API surface — verdict: OK (contract-driven), AUD-09 still open

- 22 modules registered in `backend/services/api/modules/__init__.py:37-60` (org, qa, workspace, inbox, eval, agent, skill, catalog, ingest, document, pipeline, context, conflict, governance, monitor, notify, period, audit, integration, acquire, forecast, exec); 51 `APIRouter(` files, 462 route decorators; A2 snapshot holds 461 endpoints in 21 groups (`catalogue/a2_endpoints.json`).
- Auth: Keycloak JWT verified against JWKS on every request (signature, issuer, expiry, audience), org from `org_id` claim, never a request field (`kernel/auth.py:1-16`, `JwksTokenVerifier` L100). Without `API_OIDC_ISSUER` every request is refused (`app.py:40-45`, `UnconfiguredVerifier`). Capabilities come from `org.role.capabilities` resolved per request; `X-Data-Space` adds Data Space roles (`modules/org/identity.py:83-96, 122+`). Org/user bound per transaction via `set_config('app.org_id'/'app.user_id', true)` (`libs/dataagent_db/session.py:75-81`; `kernel/db.py:23-29`).
- Capability check from A2: `endpoint("POST", "/v1/questions", …)` returns route kwargs + `Depends(require(...))` from the snapshot; a route not in A2 fails at import (`catalogue/__init__.py:1-10`). Contract tests: `tests/unit/api/test_catalogue.py` (11 tests incl. `test_the_app_as_built_passes_the_check` L93, drift/extra detection), `tests/unit/api/test_a3_contract.py` (request shapes of A3). `make api-types` regenerates the TS client from the live OpenAPI (`Makefile:92-95`).
- problem+json: `kernel/problems.py` (`Problem`, `install_problem_handlers`), 500s wrapped with trace_id (`app.py:131-146`).
- Idempotency-Key: claim row in `integration.idempotency_key` committed before handler, 24 h replay from Redis, 422 on body mismatch, 409 while in flight; **fail-open without Redis** (`kernel/idempotency.py:1-13`, `ratelimit.py:7-9`).
- Rate limit: 120/min per org+subject in Redis, headers + 429; a per-route question quota (`ratelimit.py:1-9, 65-73`). Off without `REDIS_URL` (warning only).
- ETag/If-Match: generic middleware hashes every JSON GET; PATCH/PUT/`:verb` with If-Match re-GET and 412 on mismatch; **If-Match is optional** and only `POST /v1/me/clients/{id}:confirm` requires it (`kernel/etag.py:1-16`). Only 2 modules mention it (`pipeline/routes.py`, `integration/owners.py`). **AUD-09 (mandatory version check on context PATCHes) is `todo`** (`docs/v3/04-backlog.md:225`). Table-level OCC exists only where D5.3 has version columns.
- SSE: `kernel/sse.py` with event ids, Last-Event-ID resume, 15 s heartbeat; used by qa (questions stream), agent (`/v1/runs/{id}/events`), forecast jobs, exec private channel.

## 2. Permission model — verdict: PARTIAL (design sound; no explicit cross-org RLS leak test, masking depends on policies being authored)

- Roles: `org.role.code` "AC01…AC11 or org-defined" (`alembic/sql/dataagent.sql:326`); capability set of 15 (`kernel/auth.py:30-47`); org-only capabilities admin/compliance/audit_read/exec_vault (`identity.py:44`). Data Space membership direct/group/agent with `valid_until`, steward owns (`query/scope.py:37-80`).
- `can_ask_data_space` checked **before any prompt is built**; a space without members is closed (`query/scope.py:37-47`). Loader filtering: `sqlgen/space.py:35-58` shows the model only enabled, non-deleted, non-personal datasets not covered by an active access policy; column samples excluded when `pii_label` is set (L80).
- Row policies bound to directory attributes and injected into the AST; column/mask policies rewritten into the SQL before execution (`query/executor.py:9-21, 314-357`); `governance.access_policy.kind` = row|column|mask (`models/governance.py:61`). Status: mechanism complete; coverage depends on operators authoring policies — no default PII masking of result sets (only `pii_label` on catalogue columns from `libs/pii`).
- Execution identity: source credentials from OpenBao `secret/tenants/{org}/…` with org check repeated in the worker (`query/secrets.py:1-9`); transaction `readonly=True` + `SET LOCAL statement_timeout` for PG/MySQL (`executor.py:244-245`), SQL Server relies on guard+rollback (`executor.py:9-11`); the source test refuses a login with CREATE privilege (`source_tests.py:77-90, 123-131`). Row cap 10 000, concurrency per source.
- Audit: one `audit.audit_log` row per /v1 call by the kernel (operation id, outcome ok/denied/error, no bodies) in the separate `dataagent_audit` DB with hash chain (`kernel/access_log.py:1-14`; `models/audit.py:27-60`). Agent tool calls are persisted as `agent.agent_step` rows written once at turn end by `turn_trace.py` (plan/tool/model_call steps, L69, L456-484), not into audit_log; `/mcp` writes its own audit rows.
- Tests: `tests/unit/api/test_auth.py` (12 tests: missing/invalid token, no org, capability, data-space header); `tests/integration/migrate/` 141 tests on RLS/policies (e.g. receipts of another org invisible, `test_core.py:398`). **No dedicated "org A reads org B" sweep across all 22 schemas.**

## 3. Events / orchestration — verdict: PARTIAL (3 `*_requested` events still unhandled; no dead-letter for the outbox)

- 22 `<schema>.outbox` tables (`alembic/sql/dataagent.sql`), columns id/org/aggregate/event_type/payload/created_at/published_at — **no attempts/error column** (`models/agent.py:596-611`).
- Relay: `worker_agent/events.py` `OutboxTableSource` claims `FOR UPDATE SKIP LOCKED` as `svc_outbox_relay` only for the event types the worker handles; `published_at` stamped after the handler commits. Debezium/Kafka is the ADR-06 target, not wired (`events.py:3-9`).
- Handler registry: `worker_agent/main.py:119-157` merges 38 module `HANDLERS` dicts. Of the 43 `*.requested`/`*_requested` strings the api emits, **40 have handlers; 3 do not appear anywhere in worker code**: `event_bulletin.requested` (`modules/monitor/calendars.py:548`, fuel-forecast bulletin "issue now"), `knowledge_run.requested` (`modules/context/routes_knowledge.py:934, 995`, KnowledgePageView "compile again" / lint run), `exec.publication.requested` (`modules/exec/routes.py:1065`, vault PublishTab). These answer 202 and stay unpublished forever (unclaimed rows, silent). `docs/v3/05-ui-v3.md:295` claims "every `*_requested` now has a worker" — inaccurate for these three.
- Retry: a failed batch is logged and retried on the next poll, no attempt counter, no poison-pill eviction (`main.py:273-278`); a persistently failing event blocks its batch. Handlers are idempotent by status check (`questions.py:22`, `report_runs.py:409`). Only worker-ingest has a DLQ (MinIO, `worker_ingest/dlq.py`).
- Temporal (ADR-33): one workflow `FireSchedule` (`schedules/workflow.py:26`), reconciliation every N s of 7 kinds — job, pipeline, report, quality, crawl, agent, nightly-evals (`schedules/targets.py:75`); schedule deleted/recreated on cron change; catch-up window bounded (`schedules/clock.py:1-14`). Cron parsing centralised in `libs/cron`.

## 4. Observability — verdict: GAP (DB-level trace exists; no metrics, no tracing export, Langfuse deployed but unused)

- Logging: stdlib `logging.basicConfig` (`main.py:286`); **no structlog**, no JSON formatter. No `opentelemetry`, `structlog`, `prometheus_client` or `langfuse` in any `requirements.txt`.
- Tracing: W3C `traceparent` id propagated into problem bodies/logs (`kernel/tracing.py:1-7`) but **nothing exports spans**; Langfuse+ClickHouse deployed (`deploy/k8s/dev/service/langfuse*.yaml`) with nothing sending (memory note confirms; only a `langfuse_span_id` column `models/agent.py:469`).
- Metrics: Prometheus scrapes **only itself** (`deploy/docker/monitoring/prometheus.yml`); no `/metrics` endpoint in api or workers.
- Turn trace: `worker_agent/turn_trace.py` writes `agent.agent_run` + `agent.agent_step` (plan, tools, `model_call` with role, model, tokens in/out, latency, cost from `LLM_PRICES`, default cost 0) once per turn; readable via `GET /v1/runs/{id}`, `/v1/runs/{id}/children`, `/v1/questions/{id}/steps`, `/v1/answers/{id}/lineage|receipt`. Budgets: steps 60 / tokens 400k / 30 min ceilings enforced (`runs.py:115, 776-779`); `max_cost_per_run` exists in `agent_config.limits` but cost is 0 on-prem so it is not a real limit.
- Wrong answer: operator has the receipt (`qa.answer_receipt`, replay), lineage, `error_book` entries, agent steps with SQL; no dashboards/alerts on error rates or latency. RUNBOOK §11 "What a question cost" documents the trace query.

## 5. Deployment topology — verdict: PARTIAL (single replica everything; Kafka deployed unused)

Manifests `deploy/k8s/{dev,production}` (same set; production differs in image registry, litellm replicas=2, Kong HPA 2–6 `production/service/kong.yaml:159-172`). All app Deployments replicas=1, resource limits small (api 250m/256Mi, worker-agent 250m/256Mi, worker-document 2/2Gi, python-sandbox 2/3Gi).

| Deployable | Image | Note |
|---|---|---|
| api, worker-agent, worker-ingest, worker-document, crawler, ml-forecast, python-sandbox, frontend | `dataagent/*:latest` | migrate init-container on api/workers |
| kong, keycloak, setup, migrate, eval | `dataagent/*` built images | eval Job on demand |
| litellm `ghcr.io/berriai/litellm:main-stable` | roles → `agent-fast` (planner, judge, guardrail, rewriter, suggester, visualize, orchestrator-summary, local) and `agent-main` (responder, text_to_sql, orchestrator-planner/judge) + `embedder` (`deploy/docker/litellm/config.yaml:28-159`); base URLs `LLM_FAST_BASE_URL`/`LLM_MAIN_BASE_URL` (vLLM on host, GPU outside k8s) | `main-stable` tag is unpinned |
| postgres:16 (20Gi), pgbouncer `edoburu/pgbouncer:latest` (unpinned), redis 7, qdrant 1.13.6, minio, openbao 2.6.3, temporal 1.32.1 + UI, clickhouse, kafka 3.7.0, langfuse 3, prometheus, mailpit, egress-proxy (squid) | | |

- **Kafka**: deployed, referenced in backend only as a source *type* (catalog schemas) — not used by the platform (relay reads tables). **ClickHouse**: only Langfuse's store + a source type. **Langfuse**: deployed, nothing sends. No TEI; embeddings go through LiteLLM `embedder`.
- MinIO buckets: langfuse, sandbox-audit, da-results, da-exports, da-ingest, da-raw-docs, da-doc-derived, da-forecast-models; object-locked: da-period-close, raw, da-raw-crawl (`job/minio-init.yaml:20-89`).
- Backup: CronJob every 6 h, `pg_dump` of **`PGDATABASE: dataagent` only**, 7-day retention on a PVC, `|| true` on failure (`job/postgres-backup.yaml:20-23, 50, 75-81`). `dataagent_audit`, keycloak, temporal DBs, MinIO, Qdrant, OpenBao are not backed up; restore is a manual Job (`postgres-restore.yaml`). No PDB for app pods; StatefulSets single replica; NetworkPolicies present.

## 6. Secrets — verdict: OK

- One shared client `libs/openbao` (hvac, AppRole/static token, KV v2 + Transit, no redirects, errors never echo bodies; `libs/openbao/__init__.py:1-16`), used by 15 service modules. Ansible role `k8s_openbao/tasks/configure.yml` writes policies `dataagent-secret-read-policy`, `dataagent-api-policy` (transit `exec-cond-*`), `dataagent-crawler-policy`, AppRoles ttl 1 h/max 4 h (L23-119).
- Pattern scan (password/secret/api_key literals, AWS keys, PEM blocks) over py/ts/yaml/sql/json: **nothing found**. `secrets.env`, `.openbao-keys.json` are gitignored and untracked (`.gitignore:102-105`); only `secrets.env.example` and the j2 template are tracked.

## 7. Evaluation — verdict: PARTIAL (offline gate real; online thresholds provisional, no baseline)

- `model/eval`: offline tier (policy 27 cases, sql 26, why 12, catalogs.json) with absolute bounds; online tier (intent 32, grounding 16; retrieval.jsonl 156) with **provisional** thresholds — `thresholds.json` states "no recorded baseline run against a live model yet".
- CI: `eval-gate` job runs offline tier on every PR (`ci.yml:223`); `release-gate.yml` runs both tiers on `release: published` or manual dispatch against a deployed stack; `deploy/k8s/*/job/eval.yaml` is the in-cluster on-demand Job. No nightly GitHub schedule; nightly evals of Data Space sets run from Temporal (`targets.py` kind `nightly-evals`, RUNBOOK §10c).
- Per-Data-Space set: only `deploy/docker/eval/ds-aw-cases.yaml` (61 cases, "Câu hỏi AdventureWorks"); answers are a draft, blind solving sheet exists (`docs/v3/eval/ds-aw-blind-sheet.md`) but no blind-solved results recorded.
- Recorded results: `docs/v3/eval/ds-aw-agent-eval.md` (03/11: agentic 3/60 passed, governed 3/60 — real baseline, very low); `ds-bh-sqlgen-ablation.md` (01/11, on the deleted DS-BH set: 52.5% correct); `docs/v3/06-r0-9-model-benchmark.md` + `model/bench/baselines/2026-09-22-*.json` (serving bench). `make eval-retrieval`: no recorded run found in docs.
- Real baselines: offline gate (0/0/1.0), agent-eval, sqlgen ablation (stale set), bench. Provisional: all four online metrics, bench thresholds "inconclusive=2" path.

## 8. Tests & CI — verdict: OK (blocking set is honest; several gates report-only)

- Unit: api 37 files/208 tests, worker_agent 89/598, repo 16/212, worker_document 8/68, ml_forecast 2/33, crawler 2/24, worker_ingest 2/10, contracts 2/5. Integration: api 52/711, worker_agent 62/368, migrate 11/141, crawler 2/12, worker_document 1/9, worker_ingest 2/7. e2e: 5 files in `tests/integration/test_*.py` against the deployed cluster (`make e2e`, writes data).
- CI blocking (`ci.yml`): ruff ratchet; strict mypy on 12 lib surfaces + api (L53-75); unit matrix; integration matrix; offline eval gate; tsc; vitest; k8s manifest validate; import-linter; design checklist (`tests/design_checklist.py --check`). Report-only (`|| true`): whole-tree mypy (L49), vulture, knip, strict TS, prettier, pip-audit/CVE scan (L335-356).
- Frontend: 153 test files / 824 `it(` cases; CI runs `npm run test:coverage`. Memory note (display-data-audit) records **6 forecast vitest failures on main since the `forecasting` merge cc3f03d9** (ForecastStudioPage, FuelForecastPage ×3, fuel shapes, forecast screens) — if still red, CI's frontend-test job is red on main.

## 9. Frontend — verdict: OK (all 21 A2 groups wired; a few actions never complete)

35 feature dirs; 47 `lib/v1` modules, 44 call `api.*` (`openapi-fetch` typed client); MSW mocks are dev/test only (`VITE_MOCK_V1`, `main.tsx:8-14`), and `mocks/integrated.ts:13-35` passes **all 21 A2 groups** through to the real api — no screen is mock-backed in the deployed app. One line each: access, admin, admin-access, admin-platform, agent-builder-v3, agent-ops, agents, analysis, answer-products, answer-receipt, ask, auth, crawl, data, data-prep, data-products, data-sources, documents, error-book, explore, forecast, home, knowledge, monitoring, needs-you, notifications, onboarding, ops, periods, rules, semantics, sessions, skill-authoring, skills, vault → all real API. Known partials from `05-ui-v3.md:668-675`: Dashboard (`/v1/dashboards`) and "recent changes / most asked" blocks have no endpoint; totals are page counts; ApiConnect cannot create/list REST secret_refs (operators place keys in OpenBao, L293).
Actions that 202 and never complete: KnowledgePageView/KnowledgeReviewPage "compile/lint run" (`knowledge_run.requested`), FuelForecastPage bulletin issue (`event_bulletin.requested`), Vault PublishTab publish (`exec.publication.requested`). Writeback execution stops at `approved` (`05-ui-v3.md:298`).

## 10. Integration names — none found

Case-insensitive grep over md/py/ts/tsx/yaml/yml for "Data Foundry", "Admin Portal", "KB Indexing", "AI Runtime": **zero hits**. No external systems by those names are referenced.

## 11. Dependencies & licences — verdict: PARTIAL

- Pinned: api fastapi 0.115.0, sqlalchemy 2.0.35, asyncpg 0.29.0, pydantic 2.13.5, pyjwt 2.9.0, sqlglot 25.24.5, hvac 2.4.0, qdrant-client 1.20.0; worker_agent adds pydantic-ai-slim 2.54.0, temporalio 1.34.0; worker_document drifts (asyncpg 0.30.0, pydantic 2.9.2); `redis>=5.0` unpinned in api/worker_agent. Frontend: react 19, react-query 5.104.1, openapi-fetch 0.17.0, recharts 3.8, keycloak-js 26, vite 6, vitest 2, msw 2.15, knip 5.
- Licences: `docs/research/licences.md:3` — AGPL/NC blocks and ELv2 YAML removed (03/11); **THIRD-PARTY-NOTICES not yet written** ("due with the first on-prem delivery"); no file in repo. Container tags `litellm:main-stable`, `pgbouncer:latest` unpinned.

## 12. Top 10 platform gaps for on-prem enterprise MVP

| # | Gap | Sev | Where |
|---|---|---|---|
| 1 | Backup covers only `dataagent` DB (not `dataagent_audit`, keycloak, temporal, MinIO, Qdrant, OpenBao); failure swallowed with `\|\| true`; no restore drill | High | `deploy/k8s/*/job/postgres-backup.yaml:22,75` |
| 2 | No HA: every app Deployment replicas=1, single-node Postgres/Redis/OpenBao/MinIO, no PDBs; OpenBao unseal manual | High | `deploy/k8s/production/service/*.yaml`, `database/*.yaml` |
| 3 | No metrics/traces export: Prometheus scrapes itself, no `/metrics`, no OTel, Langfuse idle; operators have only logs + DB rows | High | `deploy/docker/monitoring/prometheus.yml`, `kernel/tracing.py` |
| 4 | Outbox relay has no attempt counter/dead-letter; a failing handler blocks its batch forever; 3 `*_requested` events have no handler (silent 202) | High | `worker_agent/events.py`, `main.py:273-278`; `calendars.py:548`, `routes_knowledge.py:934`, `exec/routes.py:1065` |
| 5 | Online eval thresholds provisional with no baseline; recorded agent-eval is 3/60 passed; DS-AW answers unverified (blind sheet unsolved) | High | `model/eval/thresholds.json`, `docs/v3/eval/ds-aw-agent-eval.md` |
| 6 | Idempotency and rate limiting fail open without Redis; `redis` unpinned | Medium | `kernel/idempotency.py:1-13`, `ratelimit.py:7-9` |
| 7 | OCC: If-Match optional, AUD-09 todo; frontend does not send it — lost updates on context PATCHes | Medium | `kernel/etag.py:1-16`, `docs/v3/04-backlog.md:225` |
| 8 | Multi-tenant isolation evidence: RLS tested per-feature (141 migrate tests) but no systematic cross-org read sweep per schema/role; security tests (JWT edge cases) unit-only, CVE scan report-only | Medium | `tests/integration/migrate/`, `ci.yml:335-356` |
| 9 | Cost limits nominal: `LLM_PRICES` default 0 → `max_cost_per_run` never trips; per-question quota only via Redis; no org-level token budget | Medium | `turn_trace.py:154-156`, `runs.py:115` |
| 10 | Deployed-but-unused infra (Kafka, ClickHouse, Langfuse) and unpinned images (`litellm:main-stable`, `pgbouncer:latest`); no upgrade runbook beyond Alembic (35 migrations, `RUNBOOK.md §10`) and no THIRD-PARTY-NOTICES | Low–Medium | `deploy/k8s/*/database/kafka.yaml`, `service/litellm.yaml`, `docs/research/licences.md:3` |
