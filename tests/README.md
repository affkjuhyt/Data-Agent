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
│   ├── api/                 # one suite per service, run in that service's image:
│   ├── worker_agent/        #   a test goes in the suite of the code it exercises.
│   ├── worker_ingest/       #   There is no single image that can import every
│   ├── worker_document/     #   service (their pins conflict), so the suites are
│   ├── crawler/             #   never run together.
│   ├── contracts/           # libs/contracts (NOP-150), run in the api image (PyYAML, pinned pydantic)
│   └── repo/                # repo-wide: k8s manifests, db-init, Ansible deploy plan, migrate image,
│                            #   cross-service auth/tenant policy, model/bench and model/eval.
│                            #   Runs in the local dev-tools image (tests/dev-tools.Dockerfile)
├── fixtures/contracts/      # invalid DTO cases + compat/ pairs for the contract gate (NOP-152);
│                            #   mock/ = the Contract v1 mock's answers (NOP-151)
├── contract_mock/           # Contract v1 mock API for FE/Data/Agent (NOP-151, `make mock-api`; README.md)
├── integration/             # testcontainers; same per-service split as unit/
│   ├── _db.py               # reset_database / run_alembic / alembic_head for the shared container
│   ├── api/, worker_agent/, worker_ingest/, worker_document/, crawler/, migrate/, contracts/
│   └── test_*.py            # e2e only (`make e2e`): V3 question, agent screens, JWT audience,
│                            #   the KB replacement
├── e2e/                     # seed data for the demo agent + non-pytest harnesses
│   ├── agent_eval.py        # behavioural eval through the deployed agent
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

On top of the level, a test may carry kinds — `contract`, `security`, `resilience`,
`performance`, `ai_eval` — and `@pytest.mark.req("NOP-148", "UC07")`, the requirements
it proves. A malformed req ID fails collection. `--req-report-dir DIR` writes one JSON
per run and `python3 tests/req_coverage.py DIR/*.json` merges them into coverage per
requirement. Which acceptance criterion a test proves is declared the same way
(`pytest.mark.req("AC-…")`, [docs/v3/checklist/README.md](../docs/v3/checklist/README.md)).

## Running

### Python — unit (no Docker inside the tests)

```bash
make test-related                                # only the test files your changes since main touch
make test-changed                                # only the suites your changes since main can affect
make test-integration PYTEST_ARGS="tests/integration/api/test_qa_answers.py"   # one file
make test-unit                                   # every suite, each in its service image, in parallel
make test-unit UNIT_SUITES=crawler:crawler       # one suite
make test-unit PYTEST_ARGS="-k lock -x"          # extra pytest args
make test TEST_JOBS=1                            # unit + integration, one suite at a time
```

`tests/run_suites.py` drives all of these. Each suite runs as
`pytest -m unit tests/unit/<suite>` inside `dataagent-test/<suite>:latest`:
`localhost:5000/dataagent/<image>:latest` with the test tooling baked in by
`tests/test-image.Dockerfile`. That image is rebuilt only when the service image or
a requirements file changes, so a run no longer pays a `pip install` per suite.
The `repo` suite belongs to no service: its base is the local `dataagent/dev-tools:local`
(`make dev-tools`, which the test targets run first), named in `UNIT_SUITES` as
`repo:dataagent/dev-tools:local` — an image with a `/` is used as given, not looked up
in the registry.
The suites run at the same time and each prints its output whole when it ends,
followed by a summary with the time each took.

`make test-changed` maps the files changed since `CHANGED_BASE` (default `main`;
committed, staged, unstaged and untracked) to suites: a service's own suites plus
every suite that imports it (`SERVICE_SUITES` in `run_suites.py`), plus `repo`, which
reads every service's Dockerfile and sources; `deploy/`, `frontend/` and `model/` select
`repo` too. Anything under
`backend/libs`, `backend/alembic` or the shared test fixtures runs everything.
Before a merge, run `make test` anyway.

`make test-related` goes one step finer, for iterating: within every suite it runs only
the test files a change touches — the test files changed (and those importing them with
`from .test_x import`), the ones whose text imports a changed module (its dotted name, or
`from <package> import <leaf>`), for `backend/services/api/modules/<m>/` the api files
named `test_<m>*.py`, and any test naming a changed file (a seed under `deploy/` read by a
worker test). What no import reaches — a conftest, shared test tooling, the DDL, a
Dockerfile, requirements — still runs the suites `test-changed` would pick, whole. Test paths
in `PYTEST_ARGS` work the same way: they replace the suite's directory, each suite gets
the paths under its own `tests/<kind>/<suite>/`, and a suite none of them is under is
skipped. It reads imports, not behaviour: an api route hit only over HTTP from a file not
named after its module is not found, which `make test` before a merge covers.

Outside Docker, the equivalent is a virtualenv holding that
service's `requirements.txt` plus `requirements-test.txt` — which is what CI's
`python-test` matrix does, one job per suite. The host interpreter (3.10) is
too old for the code (3.11+).

### Python — integration (testcontainers)

```bash
make test-integration
make test-integration INTEGRATION_SUITES=api:api
```

The suite container gets the Docker socket and the host network, so
testcontainers starts Postgres/Qdrant/MinIO next to it. `requirements-integration.txt`
adds testcontainers, psycopg2 and alembic; a suite that needs more lists it in
`tests/integration/<suite>/requirements.txt`.

Every suite shares one session-scoped Postgres container. A test that needs an
empty database at alembic head takes the `migrated_db` fixture
(`tests/integration/conftest.py`): the chain runs once per container, and every
later test gets a truncate-and-reload of the rows the migrations inserted
(`tests.integration._db.migrated_database`) — about 0.2 s instead of ~3 s. A
fingerprint of the schema guards it: after a test that changed the schema (a
downgrade, its own DDL), the next one migrates from scratch again. A test that
exercises the migrations themselves starts from `reset_database`, which drops every
schema and role the migrations created; never hand-roll a `DROP SCHEMA public`.

`make test` runs both. We do NOT roll every service's deps into
`pyproject.toml [test]` — no single environment can hold them all.

### Python — e2e (deployed dev cluster)

```bash
read -r -s 'E2E_PASS?helios-engineer password: '; export E2E_PASS        # zsh/bash
# V3 question E2E uses the local `DS-DEMO` Data Space by default; override with
# `E2E_DATA_SPACE=<id-or-code>` when testing another active Data Space.
read -rsp 'platform-admin password: ' E2E_ADMIN_PASS; export E2E_ADMIN_PASS # tenant 1, JWT audience
read -rsp 'helios-admin password: ' E2E_T2_ADMIN_PASS; export E2E_T2_ADMIN_PASS # tenant 2
make e2e                                                    # every tests/integration/test_*.py
make e2e E2E_TESTS=tests/integration/test_agent_answer_e2e.py PYTEST_ARGS="-v -s"
```

`make e2e` runs `pytest -m e2e` in the worker-agent image against the dev cluster's
NodePorts (Kong `:30100`, Keycloak `:30110`). Override
`E2E_KONG_URL` / `E2E_KEYCLOAK_URL` to target a shared or
another cluster. Credentials come from the environment, never from a file
(`tests/conftest.py` `LIVE_ACCOUNTS`): each account's password variable is required
— a test fails naming the one that is unset — and its username defaults to the demo
account (`E2E_USER` helios-engineer, `E2E_ADMIN_USER` platform-admin,
`E2E_T2_ADMIN_USER` helios-admin). The load test goes through Kong as `E2E_USER`. The demo
accounts' passwords are the vault's `vault_demo_password_*`
(`deploy/ansible/inventories/<env>/group_vars/all/vault.yml.example`), which the
setup Job writes into Keycloak on a deploy with `-e apply_setup=true`; an unset
one leaves that account's password as it is. `E2E_STRICT=1` turns the model-dependent behaviour checks
from xfail into failures; each turn's frames land in `tests/e2e/out/`.

**These tests write data.** The agent-answer test runs real turns (new
conversations every run) and the tenant-isolation test deletes, revokes and
rotates API keys. Point them only at a cluster where that is acceptable.

They need the demo data the `db-init` Job loads from `deploy/docker/sql/` when the
inventory has `seed_demo: true`; after editing a seed file rerun the pipeline with
`-e apply_jobs=true` (RUNBOOK §5).

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
    from services.api.kernel.auth import CAPABILITIES  # example
    assert "ask" in CAPABILITIES
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

## Promoting a script to a test

A check worth keeping is a pytest case, one per scenario so failures show individually,
not a standalone script.

## What's intentionally NOT here yet

These need product decisions, not just code — left for the next milestone:

- **Mock Keycloak/JWT issuer for unit tests** — decide: stub the JWKS endpoint
  with `pytest-httpx`, or use `python-keycloak` with a fixture realm?
- **Frontend MSW (Mock Service Worker)** — once the OpenAPI client lands,
  generate handlers from the spec rather than hand-mocking.

Open an issue (or steal from `TODO(staff-review)` markers) before adding any
of these — they have design implications beyond test infra.
