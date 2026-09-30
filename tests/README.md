# Tests

Every Python test lives here, split by kind (unit / integration / e2e) and, for
unit and integration, by the service whose image runs it. `make test-unit`,
`make test-integration` and `make e2e` are the entry points; CI runs the same
per-service suites.

## Layout

```text
tests/
├── conftest.py              # session fixtures: postgres / redis / kafka via testcontainers
├── unit/                    # pure Python, no Docker, no network — fast
│   ├── _loader.py           # load a service module by path, with stubs that do not leak
│   ├── workflow_runtime/    # one suite per service, run in that service's image:
│   ├── kb_backend/          #   a test goes in the suite of the code it exercises;
│   ├── platform_workers/    #   pure-library, manifest, deploy and grant tests go in
│   ├── sandbox_executor/    #   workflow_runtime. There is no single image that can
│   ├── auth_api/            #   import every service (their pins conflict: OTel,
│   ├── kb_worker/         #   boto3), so the suites are never run together.
│   ├── flow_builder/
│   └── model_service/
├── integration/             # testcontainers; same per-service split as unit/
│   ├── _db.py               # reset_database / run_alembic / alembic_head for the shared container
│   ├── _sandbox.py          # sandbox-runner image gate (SANDBOX_RUNNER_IMAGE)
│   ├── workflow_runtime/    # migrations, RLS on KB child tables, memory, schema catalog,
│   │   └── requirements.txt #   handler→sandbox wiring (its extra sandbox imports)
│   ├── auth_api/            # conftest.py: the auth_api app + minimal schema; smoke + RLS
│   ├── platform_workers/    # schema crawler, web crawler
│   ├── sandbox_executor/    # real sandbox-runner containers, read-only Postgres backend
│   └── test_*.py            # e2e only (LIVE_STACK=1): agent answer, JWT audience, load, tenant isolation
├── e2e/                     # seed data for the demo agent + non-pytest harnesses
│   ├── seed.sql             # demo agent, metrics, fact_sales warehouse
│   ├── seed-governance.sql  # schema-crawler warehouse, owners, lineage, freshness SLAs
│   ├── seed-flow.js         # the demo agent's canvas in Mongo
│   ├── agent_eval.py        # 10-case behavioural eval through /api/orchestrate/stream
│   └── ui.e2e.mjs           # Playwright: real Keycloak login → ask → check the answer block
├── loadtest/                # k6 scripts and an ingestion throughput probe
└── README.md
```

Frontend tests live next to source in `frontend/src/**/__tests__/` or `frontend/src/**/*.test.tsx`
and are run by [frontend/vitest.config.ts](../frontend/vitest.config.ts).

## Markers (enforced)

Every test must declare one of:

- **`unit`** — No external services. SQLite/in-memory only. Runs every PR.
- **`integration`** — testcontainers (PG, Kafka, Redis) come up per session. Runs every PR.
- **`e2e`** — Drives the deployed dev cluster. Needs `LIVE_STACK=1` (set by `make e2e`). Not run in CI.

The unmarked-test guard in `conftest.py` will skip tests that forget. Add the
marker, don't bypass the guard.

## Running

### Python — unit (no Docker inside the tests)

```bash
make test-unit                                   # every suite, each in its service image
make test-unit UNIT_SUITES=kb_backend:kb-backend # one suite
make test-unit PYTEST_ARGS="-k lock -x"          # extra pytest args
```

Each suite runs as `pytest -m unit tests/unit/<suite>` inside
`localhost:5000/dataagent/<image>:latest` with `requirements-test.txt`
installed on top. Outside Docker, the equivalent is a virtualenv holding that
service's `requirements.txt` plus `requirements-test.txt` — which is what CI's
`python-test` matrix does, one job per suite. The host interpreter (3.10) is
too old for the code (3.11+).

### Python — integration (testcontainers)

```bash
make test-integration
make test-integration INTEGRATION_SUITES=auth_api:auth_api
```

The suite container gets the Docker socket and the host network, so
testcontainers starts Postgres/Qdrant/MinIO next to it. `requirements-integration.txt`
adds testcontainers, psycopg2 and alembic; a suite that needs more (the
workflow_runtime handler-wiring test runs sandbox_executor's app) lists it in
`tests/integration/<suite>/requirements.txt`. The sandbox tests start real
sandbox-runner containers from `SANDBOX_RUNNER_IMAGE`
(default `dataagent/sandbox-runner:latest`, the deploy build).

Every suite shares one session-scoped Postgres container. A test that needs a
clean database calls `tests.integration._db.reset_database`, which drops every
schema and role the migrations created; never hand-roll a `DROP SCHEMA public`.

`make test` runs both. We do NOT roll every service's deps into
`pyproject.toml [test]` — no single environment can hold them all.

### Python — e2e (deployed dev cluster)

```bash
export E2E_USER=helios-engineer; read -rsp 'password: ' E2E_PASS; export E2E_PASS
make e2e                                                    # every tests/integration/test_*.py
make e2e E2E_TESTS=tests/integration/test_agent_answer_e2e.py PYTEST_ARGS="-v -s"
```

`make e2e` runs `pytest -m e2e` in the `workflow_runtime` image against the dev
cluster's LAN NodePorts (Kong 30100, Keycloak 30110, workflow-runtime 30240);
override `E2E_KONG_URL` / `E2E_KEYCLOAK_URL` / `E2E_RUNTIME_URL` to target
another cluster. Credentials come from the environment (`E2E_USER`, `E2E_PASS`),
never from a file. `E2E_STRICT=1` turns the model-dependent behaviour checks
from xfail into failures; each turn's frames land in `tests/e2e/out/`.

**These tests write data.** The agent-answer test runs real turns (new
conversations every run) and the tenant-isolation test deletes, revokes and
rotates API keys. Point them only at a cluster where that is acceptable.

They need the demo data: the `seed` Job loads `tests/e2e/seed.sql`,
`seed-governance.sql` and `seed-flow.js` when the inventory has `seed_demo: true`.
The Job reads them from the `seed-sql` / `seed-mongo` ConfigMaps that
`deploy/k8s/create-configmaps.sh` builds, so after editing a seed file rerun the
pipeline with `-e apply_jobs=true` (RUNBOOK §5).

### Frontend

```bash
npm install
npm run test               # vitest run
npm run test:watch         # vitest watch
npm run test:coverage      # vitest + v8 coverage
```

## Coverage targets

Python: `fail_under = 48` in `pyproject.toml` `[tool.coverage.report]`, applied
to the combined unit report (CI's `python-coverage` job merges the per-suite
data files; a single suite passes `--cov-fail-under=0`). It was set to the
measured 53.2 % minus 5 and only ever goes up.

Frontend: the global thresholds in `frontend/vitest.config.ts` are still **0**;
modules get a per-file threshold there once they have real tests.

Coverage that doesn't ratchet silently rots; coverage that ratchets too fast
blocks shipping.

## Writing a new unit test

```python
import pytest

@pytest.mark.unit
def test_user_role_serialization():
    from services.shared.auth import serialize_role  # example
    assert serialize_role("platform-admin") == "platform_admin"
```

## Writing a new integration test (Postgres-backed)

```python
import pytest

@pytest.mark.integration
def test_tenant_query_isolates(postgres_container):
    dsn = postgres_container["dsn"]
    # apply alembic migrations to this fresh DB, then test query
    ...
```

The `postgres_container` fixture is session-scoped — one container per pytest
run, not per test. If you need per-test isolation, wrap your test in a
transaction and roll back at the end.

## Service-scoped integration tests — the auth_api pattern

`tests/integration/auth_api/conftest.py` is the template for in-process integration
testing of a FastAPI service. See [tests/integration/auth_api/test_auth_api_smoke.py](integration/auth_api/test_auth_api_smoke.py)
for the seven smoke tests built on top of it.

The pattern, when cloning for a new service (workflow_runtime, kb-backend, ...):

1. **Import the service by its package path** — `from services.auth_api.app
   import create_app` — the way the image runs it (PYTHONPATH=backend, which
   pyproject's `pythonpath` already gives pytest). Do not put a service
   directory on `sys.path`: auth_api has its own `services/` package, and with
   its directory first every later `import services.…` in the session
   resolved to it.

2. **Apply a minimal SQL schema, not full alembic.** Hand-roll only the
   tables the routes under test touch. Faster, easier to read, and the
   schema lives in the same file as the fixture — when columns change, the
   test breaks loudly. Don't drift toward "test fixture = full prod schema"
   — that ratchet ends with tests blocked by every unrelated migration.

3. **Set env vars BEFORE importing the service module.** Module-level
   constants (e.g. `PG_DSN = os.environ.get(...)`) read at import time, so
   the session-scoped app fixture sets `DATABASE_URL`/`APP_DATABASE_URL`
   first and imports second.

4. **Strip the startup hook**. Most services have a `@app.on_event("startup")`
   that talks to Kong, OpenBao, or Kafka. None are appropriate in-process.
   `app.router.on_startup.clear()` after `create_app()`.

5. **Reset per test, don't recreate the container.** Session-scoped
   container, function-scoped `auth_api_db` that calls `reset_database` and
   re-applies the minimal schema. Other suites migrate the same container,
   so anything set up once per session (tables, the `app_user` password) is
   gone by the time a later test runs.

6. **Mock external services only where you must.** The smoke tests in
   `test_auth_api_smoke.py` deliberately stop at validation/auth so they
   don't need OpenBao at all. When you DO need OpenBao mocked, patch
   `auth_core.get_vault` at test scope, not in conftest — keeps the global
   fixture clean.

7. **Add the suite to CI and make**. A new `tests/integration/<service>/`
   directory goes into `INTEGRATION_SUITES` in the Makefile and the
   `python-integration` matrix in `.github/workflows/ci.yml`.

## Row-Level Security (RLS) — the rollout pattern

`backend/alembic/versions/a1b2c3d4e5f6_enable_tenant_rls.py` enables `FORCE ROW
LEVEL SECURITY` on every public table with a `tenant_id` column and
installs a `tenant_isolation` policy filtering by
`current_setting('app.tenant_id')`.

After the migration, services have two ways to query the DB:

1. **As `app_user`** — RLS-enforced. Connection MUST set `app.tenant_id`
   per request. Forgetting to set it returns zero rows (fail-closed).
   Try to read/write another tenant's row → empty result / CheckViolation.
2. **As the owner role** (existing `aeroflow` etc.) — bypasses RLS via the
   superuser bit. Reserved for migrations, backups, audit aggregation.
   Service routes MUST NOT use this path for user-facing reads.

### How to wire a route — the auth_api PoC

[backend/services/auth_api/routers/api_keys.py](../backend/services/auth_api/routers/api_keys.py)
shows the pattern as a sibling route:

```python
from shared.db_context import get_tenant_db

@router.get("/api/auth/api-keys/list-via-rls")
def list_api_keys_via_rls(db = Depends(get_tenant_db), ...):
    with db.cursor(...) as cur:
        cur.execute("SELECT ... FROM api_keys")   # no WHERE — RLS handles it
        return ...
```

The `get_tenant_db` dependency:

- Reads `X-Tenant-Id` from the headers Kong injects after JWT verification.
- Validates it's a UUID (rejects SQL injection-via-SET at the boundary).
- Opens a `psycopg2` connection to `APP_DATABASE_URL` (the app_user DSN).
- `SET LOCAL app.tenant_id` for the lifetime of the transaction.
- Yields the connection; commits on clean exit, rolls back on exception.

### Rollout checklist for each service

When migrating a service (workflow_runtime, flow_builder, kb-backend, ...):

1. Give it `APP_DATABASE_URL` in its `<workload>-secrets` (Ansible
   `secrets.yml` → `create-secrets.sh`). Format:
   `postgresql://app_user:<password>@<pg-host>:<port>/<db>`. The password
   comes from the vault — the migration creates the role without one.
2. Add `from shared.db_context import get_tenant_db` to the routers.
3. Replace `def handler(... = Depends(get_db))` with
   `def handler(db = Depends(get_tenant_db))`.
4. Drop the `WHERE tenant_id = %s` from queries that the new dep covers.
   Keep them for now if you want belt-and-suspenders — RLS is the safety
   net, app-layer WHERE is the primary filter.
5. Run `pytest -m integration tests/integration/auth_api/test_rls_enforcement.py`
   in CI to prove no regression.

### Testing the contract

[test_rls_enforcement.py](integration/auth_api/test_rls_enforcement.py) pins six
properties of the migration. Don't delete tests when refactoring policies
— update the assertions if the contract changes intentionally:

1. `app_user` SELECT * (no WHERE) returns only the bound tenant's rows.
2. `app_user` without setting context returns zero rows (fail-closed).
3. `app_user` INSERT with a foreign tenant_id is denied by WITH CHECK.
4. Owner/superuser bypasses RLS (migrations + admin tasks still work).
5. `get_tenant_db` rejects non-UUID `X-Tenant-Id` with 422.
6. End-to-end: the `/api/auth/api-keys/list-via-rls` PoC route returns
   only the caller's row.

### Common gotchas

- **`current_setting('app.tenant_id', true)`** — the second arg
  (`missing_ok=true`) returns `''` if unset rather than raising. The
  policy uses `NULLIF` to translate `''` to NULL so the comparison
  cleanly fails (no rows).
- **`SET LOCAL`** vs `SET` — always use LOCAL inside the dep. Without it,
  connection-pool reuse would leak tenant context across requests.
- **Long-running connections** — if you batch operations on one connection
  across multiple tenants, re-call `set_tenant_context` between batches.
  Forgetting = previous tenant's rows visible. Cleanest is one conn per
  tenant op.
- **RLS violations are "InsufficientPrivilege", not "CheckViolation"** —
  Postgres treats RLS as access control, not data validation. SQLSTATE is
  42501 (`InsufficientPrivilege`), not 23514 (`CheckViolation`). If a
  route handler catches `IntegrityError` and converts to a friendly 4xx,
  it WON'T catch the RLS case — that crashes as 500 unless you also catch
  `psycopg2.errors.InsufficientPrivilege` explicitly. Convert to 403 with
  a "not your tenant" message.

## Promoting a script to a test

The old standalone tenant-isolation runner is now
[tests/integration/test_tenant_isolation.py](integration/test_tenant_isolation.py),
split into one pytest case per scenario so failures show individually. New
checks go here as tests, not as scripts.

## What's intentionally NOT here yet

These need product decisions, not just code — left for the next milestone:

- **Mock Keycloak/JWT issuer for unit tests** — decide: stub the JWKS endpoint
  with `pytest-httpx`, or use `python-keycloak` with a fixture realm?
- **Frontend MSW (Mock Service Worker)** — once the OpenAPI client lands,
  generate handlers from the spec rather than hand-mocking.

Open an issue (or steal from `TODO(staff-review)` markers) before adding any
of these — they have design implications beyond test infra.
