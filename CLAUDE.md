# Working rules for this repo (humans and AI agents)

Read this before changing anything. This file owns conventions, cleanup policy and the
command surface.

## 1. Command surface

`make help` lists everything. Use the targets — they mirror `.github/workflows/ci.yml`,
so a green `make ci` is a green pipeline.

| Task | Command | Notes |
| --- | --- | --- |
| Lint / format | `make lint`, `make fmt` | ruff, run inside the service image. Import sorting (`I001`) is on and `combine-as-imports` is off, so ruff writes one `from x import (y as z,)` statement per alias — that is its output, not churn; do not hand-collapse it |
| Types | `make type` | mypy (`pyproject.toml` config, not strict) on the whole of `backend/libs` + `backend/services` — red, see below. CI's blocking step is the strict per-surface list in `.github/workflows/ci.yml` |
| Tests | `make test-unit`, `make test-integration`, `make test` | one pytest suite per service (`tests/unit/<service>/`, `tests/integration/<service>/`), each run in **that service's image** — no single image can import every service. **Host Python is 3.10, the code targets 3.11**: never run tests with the host interpreter. One suite: `make test-unit UNIT_SUITES=kb_backend:kb-backend` |
| Dead code | `make deadcode` | vulture (python, `min_confidence = 60`, vetted exceptions in `backend/.vulture_whitelist.py`, both set in `pyproject.toml`) + knip (frontend) |
| Frontend | `make frontend-lint`, `make frontend-test` | tsc + vitest. There is **no ESLint**: the config was v8-format, unreferenced and never ran, so it and its five devDeps were removed. Correctness is tsc's job, style is prettier's, unused code is knip's. Reintroducing ESLint means a flat `eslint.config.js`, an npm script and a CI step — all three or none |
| End-to-end | `make e2e` | `pytest -m e2e` against the **deployed dev cluster** over its LAN NodePorts (override with `E2E_KONG_URL` / `E2E_KEYCLOAK_URL` / `E2E_RUNTIME_URL`); `E2E_USER` / `E2E_PASS` from the environment. It **writes data**: agent turns create conversations, the tenant-isolation test deletes, revokes and rotates API keys. Needs the `seed_demo` data. See `tests/README.md` |
| Serving benchmark | `make bench` | TTFT / decode / throughput per LLM role against the GPU tiers (`model/bench/`). Needs `LLM_MAIN_BASE_URL` + `LLM_FAST_BASE_URL` reachable; pass args with `BENCH_ARGS="…"`. Measures cost, not quality — `model/eval` owns quality |
| Serving gate | `make bench-gate` | judges a bench report against `model/bench/thresholds.json`. Exit 0 pass / 1 fail / **2 inconclusive** — 2 means a budget could not be judged (shared GPU, missing metrics), re-run rather than widen |
| Agent e2e latency | `make bench-e2e` | per-stage breakdown of one real question against a deployed agent. Needs `BENCH_AGENT_URL`, `BENCH_AGENT_ID`, `BENCH_TENANT_ID`, `GATEWAY_KEY` exported — never read from `.env` |
| Deploy | `make deploy` | Ansible is the only deploy path; it deploys **committed** changes only |
| Diagrams | `make diagrams` | renders `docs/v2/diagrams/*.puml`; PlantUML pinned to 1.2024.8 and how to check the SVGs for errors: `docs/v2/diagrams/README.md` |

### Gates that are currently red

Do not claim "lint is green" — it is not, and it is not your change that did it.
Measured on 28/09/2026 in the `workflow_runtime` image with ruff 0.6.9:

| Gate | State | Meaning |
| --- | --- | --- |
| `ruff check .` | 1531 errors (868 auto-fixable) | never enforced; CI's `python-lint` job fails on `main` |
| `ruff format --check .` | 514 of 620 files differ | the tree was never formatted with ruff |
| `mypy` (whole tree) | 311 errors in 66 files; reported only (`|| true`) | only the strict per-surface list in `ci.yml` blocks |

Tests are **not** on this list: `make test-unit` is green (8 suites, 2120 passed on
28/09/2026) and `make test-integration` is green (4 suites). A failing test is a real
regression — fix it or explain it, do not wave it through as pre-existing.

Fixing the lint gates is worth doing, but as its own commit: a repo-wide `ruff format .`
touches ~514 files and must not ride along with a behaviour change. Until then, judge your work
by "no *new* findings in the files you touched", verified against a baseline worktree.

## 2. Before you delete anything

The test suites are green per service, so a test that fails after your change is
evidence against it. Lint and whole-tree mypy are not clean (§1), so for those
"there are findings" proves nothing — compare against a baseline instead:

```bash
git worktree add --detach /tmp/base <commit-before-your-change>
# run the same command in /tmp/base and in the working tree, diff the summary lines
```

Delete only with evidence: `ruff --select F401,F811,F841`, `vulture`, `knip`, plus a
repo-wide grep for the symbol **and** for its string form (dynamic imports, Kafka topic
names, k8s manifests, shell scripts, docs). Deleted code is recoverable from git —
commented-out code is not allowed to stay.

## 3. Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Python modules, packages, functions, vars | `snake_case` | `warehouse_secrets.py`, `run_input_refine` |
| Python classes | `PascalCase` | `AnalyticalSlots` |
| Private helpers | leading `_`, module-local; do **not** re-export them from `__init__.py` | `_parse_source` |
| React components | `PascalCase.tsx`, one component per file | `AgentPlayground.tsx` |
| Hooks | `useThing.ts` | `useAgentStream.ts` |
| Other TS modules | `camelCase.ts` | `apiClient.ts`, `formatVN.ts` |
| Kubernetes manifests, k8s objects, Kafka topics | `kebab-case` | `workflow-runtime.yaml`, `observation.published` |
| Docs | `SCREAMING-KEBAB.md` at `docs/`, lowercase inside feature folders | `docs/RUNBOOK.md` |
| Env vars | `SCREAMING_SNAKE`, prefixed by subsystem | `LITELLM_BASE_URL` |

Known inconsistency, do not copy it: container images are half snake_case
(`dataagent/workflow_runtime`) and half kebab (`dataagent/kb-backend`), while every k8s
object is kebab. New services use kebab everywhere outside Python; renaming the existing
images is a deploy-affecting change that needs its own PR (manifests + Ansible + docs in
one commit).

Names say what the thing is, not how it is implemented; no Vietnamese identifiers, no
abbreviations that are not already in the domain (`kb`, `llm`, `dw` are fine).

## 4. Comments

Comments explain **why**, never what the line already says. Keep the existing density:
a short block above non-obvious logic, and a note whenever a workaround exists (what
broke, what the fix is tied to). Write them in the language the surrounding file uses.

## 5. Shell scripts

No new `.sh` for orchestration. A new operational step goes, in order of preference:

1. a `make` target,
2. an Ansible task/role (`deploy/ansible/`) — it already owns deploy, secrets, OpenBao,
3. declarative config consumed by an existing tool (Kong → decK, Kafka topics → a topic
   spec, k8s → a manifest/Job),
4. a small Python CLI under the service that owns it.

Bash stays only for host bootstrap that runs before those tools exist. The per-script
plan is the appendix "Kế hoạch bỏ script" of [`docs/v2/11-ops.md`](docs/v2/11-ops.md).

## 6. Secrets

Never commit secrets. Application secrets live in OpenBao; deploy-time secrets come from
`ansible-vault` (`deploy/ansible/inventories/*/.secrets/`). `.env`, `secrets.env` and
`*.key` stay untracked. When a script needs a credential it reads it from the vault, not
from a literal.

## 7. Repo layout

```
backend/libs        shared libraries (basemodel, database_connector, shared, ingestion,
                    semantic, indicator)
backend/services    one deployable per directory, Dockerfile inside. kb_backend also holds the four KB
                    Kafka workers (workers/*), built as the `kb-worker` image (deploy/docker/kb-worker);
                    llm-exporter is a platform_workers worker. Add a worker, not a service, for a new
                    queue consumer

                    workflow_runtime is layered: api/ (routers, auth, app factory) > application/
                    (nodes, orchestrator, runtime_services, graph) > infrastructure/ (db, mongo,
                    redis, kafka, qdrant clients, repositories) > domain/ (state, schemas). A lower
                    layer never imports a higher one — .importlinter enforces it. main.py and jobs/
                    are entrypoints outside the layers; prompts/ holds the templates (PROMPTS_DIR).
                    It also serves agent/workflow/canvas authoring (ex flow_builder)

                    Kafka events go through the outbox (libs/shared/outbox.py, docs/RUNBOOK.md §10b):
                    a service writes the event to OutboxEvents — in its own transaction when it has
                    one — and its relay publishes it. Do not add a direct publish after a commit.
                    Concurrent writers of one aggregate lock the parent row (FOR UPDATE) and the
                    "at most one active/published" rules are partial unique indexes
backend/alembic     Postgres migrations (the `migrate` image)
frontend/src        React app (features/, components/, lib/)
deploy/ansible      the deploy path (k8s-local inventory)
deploy/k8s          manifests: dev/ and production/
deploy/docker       build contexts for the keycloak, kong and setup images; files the
                    ConfigMaps and Jobs read (sql, kong, keycloak, litellm, monitoring)
model/eval          release gate: cases, scorers, thresholds
model/bench         serving benchmark + gate, agent e2e latency breakdown
tests/unit/<service>/, tests/integration/<service>/   one suite per service image
tests/integration/test_*.py                         e2e tests (make e2e)
tests/e2e           demo seed data (k8s seed Job), eval and Playwright harnesses
tests/loadtest      k6
docs                RUNBOOK, SECURITY, SILVER-FORMAT, v2/ (current design; v2/diagrams/ PlantUML sources + SVG)
```

## 8. Definition of done

- While lint is red (§1): no new ruff or mypy findings in the files you touched
  (baseline worktree, §2); the strict mypy surfaces and `make frontend-lint` clean;
  `make test-unit` (and `make test-integration` when you touched what it covers) green.
- No new `# TODO` without an owner and a follow-up issue.
- Docs updated in the same commit when behaviour, commands or layout change.
- Deleted code is deleted, not commented out.

## 9. V2 work (Data Agent POC, oil/gold)

Any work on the V2 data/knowledge/agent platform starts at `docs/v2/README.md`.
`docs/v2/00-architecture-v2.md` is the source of truth; its twelve invariants (immutable raw,
structured data never goes through chunk/embedding, LLM proposes and deterministic checks plus a
human decide, point-in-time by `available_at`, OCC instead of last-write-wins, …) are not
negotiable per task. Pick the next item from `docs/v2/05-implementation-plan.md` in STEP order and
update its `TT` column in the same commit that finishes it.
