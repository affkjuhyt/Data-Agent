# Tests

Foundation set up Jun 2026. The goal of this directory is to make it cheap to
write the **next** test — the first 10 tests are placeholders so the harness
is exercised; the next 100 should land here, not in `deploy/docker/scripts/`.

## Layout

```text
tests/
├── conftest.py              # session fixtures: postgres / redis / kafka via testcontainers
├── unit/                    # pure Python, no Docker, no network — fast
│   └── test_sanity.py
├── integration/             # spins testcontainers OR drives live stack (e2e marker)
│   ├── conftest.py          # per-service fixtures (auth-api app + DB schema)
│   ├── test_auth_api_smoke.py
│   └── test_tenant_isolation.py
└── README.md
```

Frontend tests live next to source in `frontend/src/**/__tests__/` or `frontend/src/**/*.test.tsx`
and are run by [vitest.config.ts](../vitest.config.ts).

## Markers (enforced)

Every test must declare one of:

- **`unit`** — No external services. SQLite/in-memory only. Runs every PR.
- **`integration`** — testcontainers (PG, Kafka, Redis) come up per session. Runs every PR.
- **`e2e`** — Drives a fully-running docker-compose stack. Needs `LIVE_STACK=1`. Runs only on push to `main`.

The unmarked-test guard in `conftest.py` will skip tests that forget. Add the
marker, don't bypass the guard.

## Running

### Python — unit only (fast, no Docker)

```powershell
pip install -e ".[test]"
pytest -m unit
```

### Python — unit + integration (testcontainers spins Docker)

```powershell
pytest -m "unit or integration"
```

Requires Docker Desktop running. First run pulls postgres:16, redis:7,
cp-kafka:7.5.0 (~500 MB total, cached afterward).

**Per-service runtime deps**: integration tests import service code
in-process, so you also need that service's runtime deps installed:

```powershell
# auth-api smoke tests need hvac, psycopg2-binary, bcrypt, structlog, ...
pip install -r backend/services/auth-api/requirements.txt
```

CI does this in the `python-test` job; expand the install list as more
service test suites land. We do NOT roll every service's deps into
`pyproject.toml [test]` — that would create a meta-package that no single
service really uses and slow installs for unit-only runs.

### Python — e2e (live stack)

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.local.yml up -d --build
bash deploy/docker/kong/kong-setup.sh
$env:LIVE_STACK = "1"
pytest -m e2e tests/integration -v
```

### Frontend

```powershell
npm install
npm run test               # vitest run
npm run test:watch         # vitest watch
npm run test:coverage      # vitest + v8 coverage
```

## Coverage targets

Coverage thresholds in `vitest.config.ts` and `pyproject.toml` are currently
**0**. This is deliberate — failing day-one CI on a 60% target would block
every PR. The intended ratchet:

| Date        | Python `backend/services/` | Python `basemodel/` | Frontend `frontend/src/` |
| ----------- | ------------------ | ------------------- | --------------- |
| Day 0 (now) | 0%                 | 0%                  | 0%              |
| +30d        | 20%                | 30%                 | 15%             |
| +60d        | 40%                | 50%                 | 30%             |
| +90d        | 60%                | 65%                 | 45%             |

Update the threshold in `[tool.coverage.report]` and `vitest.config.ts` ->
`coverage.thresholds` at each ratchet point. Coverage that doesn't ratchet
silently rots; coverage that ratchets too fast blocks shipping.

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

## Service-scoped integration tests — the auth-api pattern

`tests/integration/conftest.py` is the template for in-process integration
testing of a FastAPI service. See [tests/integration/test_auth_api_smoke.py](integration/test_auth_api_smoke.py)
for the seven smoke tests built on top of it.

The pattern, when cloning for a new service (workflow-runtime, kb-backend, ...):

1. **Mirror the Dockerfile WORKDIR with `sys.path`**. Services use top-level
   imports like `from auth_core import *`. Re-create that layout in conftest
   so the test process can import the service as the container would:

   ```python
   sys.path.insert(0, str(REPO_ROOT / "services" / "<service-name>"))
   sys.path.insert(0, str(REPO_ROOT / "services"))   # for shared/
   ```

2. **Apply a minimal SQL schema, not full alembic.** Hand-roll only the
   tables the routes under test touch. Faster, easier to read, and the
   schema lives in the same file as the fixture — when columns change, the
   test breaks loudly. Don't drift toward "test fixture = full prod schema"
   — that ratchet ends with tests blocked by every unrelated migration.

3. **Patch env vars BEFORE importing the service module.** Module-level
   constants (e.g. `PG_DSN = os.environ.get(...)`) read at import time. Set
   `os.environ["DATABASE_URL"]` in the fixture, then `import auth_core`,
   then re-assign `auth_core.PG_DSN` defensively in case anything pre-read.

4. **Strip the startup hook**. Most services have a `@app.on_event("startup")`
   that talks to Kong, OpenBao, or Kafka. None are appropriate in-process.
   `app.router.on_startup.clear()` after `create_app()`.

5. **TRUNCATE per test, don't recreate the container.** Session-scoped
   container, function-scoped truncate. Each test reasons about an empty
   table without paying for `docker run` 50 times.

6. **Mock external services only where you must.** The smoke tests in
   `test_auth_api_smoke.py` deliberately stop at validation/auth so they
   don't need OpenBao at all. When you DO need OpenBao mocked, patch
   `auth_core.get_vault` at test scope, not in conftest — keeps the global
   fixture clean.

7. **Add service runtime deps to CI**. Update `.github/workflows/ci.yml`
   `python-test` job to `pip install -r backend/services/<your-service>/requirements.txt`.

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

### How to wire a route — the auth-api PoC

[backend/services/auth-api/routers/api_keys.py](../services/auth-api/routers/api_keys.py)
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

When migrating a service (workflow-runtime, flow-builder, kb-backend,
release-worker, ...):

1. Set `APP_DATABASE_URL` env var in its compose entry. Format:
   `postgresql://app_user:<password>@postgres:5432/<db>`. The password is
   set out-of-band (vault, k8s secret) — the migration creates the role
   without one.
2. Add `from shared.db_context import get_tenant_db` to the routers.
3. Replace `def handler(... = Depends(get_db))` with
   `def handler(db = Depends(get_tenant_db))`.
4. Drop the `WHERE tenant_id = %s` from queries that the new dep covers.
   Keep them for now if you want belt-and-suspenders — RLS is the safety
   net, app-layer WHERE is the primary filter.
5. Run `pytest -m integration tests/integration/test_rls_enforcement.py`
   in CI to prove no regression.

### Testing the contract

[test_rls_enforcement.py](integration/test_rls_enforcement.py) pins six
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

The previous `deploy/docker/scripts/test_tenant_isolation.py` was a standalone runner.
It's now [tests/integration/test_tenant_isolation.py](integration/test_tenant_isolation.py),
split into one pytest case per scenario so CI shows failures individually.

The original path is now a shim that delegates to pytest, so existing runbooks
or hooks pointing at the old location keep working during the transition.

## What's intentionally NOT here yet

These need product decisions, not just code — left for the next milestone:

- **Mock Keycloak/JWT issuer for unit tests** — decide: stub the JWKS endpoint
  with `pytest-httpx`, or use `python-keycloak` with a fixture realm?
- **Alembic fixture for schema setup** — needs to be deterministic across the
  72 migrations; today applying them all per session is too slow.
- **Frontend MSW (Mock Service Worker)** — once the OpenAPI client lands,
  generate handlers from the spec rather than hand-mocking.

Open an issue (or steal from `TODO(staff-review)` markers) before adding any
of these — they have design implications beyond test infra.
