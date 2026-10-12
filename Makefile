# One entry point for the commands that CI, the deploy playbook and the docs
# already run. Targets are thin wrappers: they must stay equivalent to what
# .github/workflows/ci.yml does, so a green `make ci` means a green pipeline.
SHELL := /bin/bash
.DEFAULT_GOAL := help
PY_IMAGE ?= localhost:5001/dataagent/api:latest
DOCKER_RUN_PY = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE)
# ruff, mypy and import-linter preinstalled (tests/dev-tools.Dockerfile): a cache hit after
# the first build, so lint runs in seconds instead of re-installing tools from PyPI each time.
DEV_TOOLS_IMAGE ?= dataagent/dev-tools:local
DOCKER_RUN_DEV = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(DEV_TOOLS_IMAGE)

.PHONY: help lint type test test-unit test-integration test-changed deadcode frontend-lint frontend-test ci e2e \
        deploy deploy-local fmt bench bench-gate bench-e2e mock-api contract-compat design-checklist design-audit api-types dev-tools fmt-files \
        type-v3 lint-imports

help:  ## List targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# The tree is not ruff-clean, so lint fails only on findings added since the
# merge-base with LINT_BASE (tests/ruff_ratchet.py), working-tree edits included.
LINT_BASE ?= main
dev-tools:  ## build the cached lint/type image (tests/dev-tools.Dockerfile); other targets call it
	@docker build -q -f tests/dev-tools.Dockerfile -t $(DEV_TOOLS_IMAGE) . >/dev/null

lint: dev-tools  ## ruff: no new findings or unformatted files since LINT_BASE (same as CI python-lint)
	@base_dir=$$(mktemp -d) && trap 'rm -rf "$$base_dir"' EXIT && \
	  git archive $$(git merge-base HEAD $(LINT_BASE)) -- pyproject.toml '*.py' | tar -x -C "$$base_dir" && \
	  docker run --rm --user 0 -v "$(PWD):/src" -v "$$base_dir:/base:ro" -w /src $(DEV_TOOLS_IMAGE) \
	    python tests/ruff_ratchet.py --base-dir /base

fmt: dev-tools  ## ruff format + autofix
	$(DOCKER_RUN_DEV) sh -c "ruff check --fix . && ruff format ."

# Format and check only the files given, e.g. FILES="backend/services/worker_agent tests/unit/worker_agent".
fmt-files: dev-tools  ## ruff format + check on FILES only (fast, for the files you touched)
	$(DOCKER_RUN_DEV) sh -c "ruff format $(FILES) && ruff check --fix $(FILES)"

type-v3: dev-tools  ## strict mypy on the V3 blocks (api, worker_agent), as CI's blocking step
	$(DOCKER_RUN_DEV) mypy --disallow-untyped-defs --disallow-any-generics \
	  --ignore-missing-imports --follow-imports=silent backend/services/api backend/services/worker_agent

lint-imports: dev-tools  ## import-linter contracts (.importlinter), as CI
	docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend $(DEV_TOOLS_IMAGE) lint-imports

type:  ## mypy strict on libs + services (same set as CI python-type)
	$(DOCKER_RUN_PY) sh -c "pip install -q mypy==2.4.0 && mypy backend/libs backend/services"

# One suite per service, each run in that service's own image: no single image
# can import every service (their OTel/boto3 pins conflict). suite:image pairs;
# tests/unit/<suite> and tests/integration/<suite> are the directories.
IMAGE_REGISTRY ?= localhost:5001
UNIT_SUITES ?= kb_backend:kb-backend platform_workers:platform-workers \
               sandbox_executor:sandbox_executor auth_api:auth_api kb_worker:kb-worker \
               model_service:model_service contracts:kb-backend api:api worker_agent:worker-agent
INTEGRATION_SUITES ?= sandbox_executor:sandbox_executor \
                      contracts:kb-backend api:api migrate:migrate worker_agent:worker-agent
# The integration sandbox tests start real sandbox-runner containers; the tag
# differs per host (tests/integration/_sandbox.py), override it to match.
SANDBOX_RUNNER_IMAGE ?= dataagent/sandbox-runner:latest
PYTEST_ARGS ?=
TEST_JOBS ?= 0
CHANGED_BASE ?= main
# --user 0, no bytecode and no pytest cache: the checkout is mounted, and a run must
# not leave root-owned files in it.
DOCKER_RUN_SUITE = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src \
                   -e PYTHONDONTWRITEBYTECODE=1 -e OTEL_SDK_DISABLED=true
# tests/run_suites.py builds each suite's cached test image (the service image plus
# test tooling, tests/test-image.Dockerfile) and runs the suites in parallel;
# TEST_JOBS=1 runs them one after another. Every suite runs even when another fails.
RUN_SUITES = UNIT_SUITES="$(UNIT_SUITES)" INTEGRATION_SUITES="$(INTEGRATION_SUITES)" \
             IMAGE_REGISTRY="$(IMAGE_REGISTRY)" SANDBOX_RUNNER_IMAGE="$(SANDBOX_RUNNER_IMAGE)" \
             PYTEST_ARGS="$(PYTEST_ARGS)" python3 tests/run_suites.py --jobs $(TEST_JOBS)

test-unit:  ## pytest unit, one suite per service in that service's image, in parallel
	@$(RUN_SUITES) unit

test-integration:  ## pytest integration (testcontainers), one suite per service image, in parallel
	@$(RUN_SUITES) integration

test:  ## unit + integration suites, all at once (same suites as CI python-test / python-integration)
	@$(RUN_SUITES) all

test-changed:  ## only the suites the changes since CHANGED_BASE (default main) can affect
	@$(RUN_SUITES) changed --base $(CHANGED_BASE)

# Tab 7 F3: the frontend's types come from the api's own OpenAPI (ADR-48), never written by hand.
# The api image has the dependencies; backend/ is mounted so the dump reflects the working tree.
api-types:  ## dump the api OpenAPI to frontend/src/lib/v1/openapi.json and generate schema.ts from it
	docker run --rm -v "$(PWD)/backend:/app:ro" -e API_OIDC_ISSUER=http://unused $(IMAGE_REGISTRY)/dataagent/api:latest \
	  python -c "import json, sys; from services.api.app import app; json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)" \
	  > frontend/src/lib/v1/openapi.json
	npm --workspace frontend exec -- openapi-typescript src/lib/v1/openapi.json -o src/lib/v1/schema.ts

# Stdlib only and reads files, like tests/run_suites.py, so it runs on the host interpreter.
design-checklist:  ## rewrite docs/v3/10-design-checklist.md: design coverage from code evidence + status.tsv
	python3 tests/design_checklist.py

design-audit:  ## fail when 10-design-checklist.md is stale or a declared status contradicts the evidence
	python3 tests/design_checklist.py --check

deadcode:  ## vulture (python) + knip (frontend)
	$(DOCKER_RUN_PY) sh -c "pip install -q vulture && vulture"
	npm --workspace frontend run deadcode

frontend-lint:  ## tsc --noEmit
	npm --workspace frontend run typecheck

frontend-test:  ## vitest
	npm --workspace frontend run test

ci: lint type test frontend-lint frontend-test contract-compat design-audit  ## everything CI runs, locally

# Same check as the CI contract-compat job (NOP-152). Locally HEAD is a branch, not
# a merge commit, so the agreed contract is the one at the merge-base. Exit 2 (the
# base has no generated/ yet) is a notice, as in CI.
CONTRACT_BASE ?= origin/main
CONTRACT_BASE_DIR := .contract-base
contract-compat:  ## contract compatibility gate against CONTRACT_BASE (libs/contracts/COMPATIBILITY.md)
	@rm -rf $(CONTRACT_BASE_DIR) && mkdir -p $(CONTRACT_BASE_DIR)
	@base=$$(git merge-base HEAD $(CONTRACT_BASE)) && \
	  if git cat-file -e $$base:backend/libs/contracts/generated 2>/dev/null; then \
	    git archive $$base backend/libs/contracts/generated | tar -x -C $(CONTRACT_BASE_DIR); \
	  fi
	@$(DOCKER_RUN_PY) python -m libs.contracts.compat \
	  --base $(CONTRACT_BASE_DIR)/backend/libs/contracts/generated \
	  --head backend/libs/contracts/generated; \
	  code=$$?; rm -rf $(CONTRACT_BASE_DIR); \
	  if [ $$code -eq 2 ]; then echo "base has no published contract, nothing to compare"; exit 0; fi; \
	  exit $$code

# In the kb-backend image, where the contracts suite runs (pinned pydantic); it also
# spares a frontend developer a Python 3.11 environment of their own.
MOCK_API_PORT ?= 8090
mock-api:  ## Contract v1 mock API on :8090 for FE/Data/Agent (tests/contract_mock/README.md)
	docker run --rm -p $(MOCK_API_PORT):$(MOCK_API_PORT) -v "$(PWD):/src" -w /src \
	  -e PYTHONPATH=/src/backend:/src -e PYTHONDONTWRITEBYTECODE=1 \
	  $(IMAGE_REGISTRY)/dataagent/kb-backend:latest \
	  python -m tests.contract_mock --host 0.0.0.0 --port $(MOCK_API_PORT)

# The serving benchmark talks to the GPU host over the LAN and, for
# --target litellm, to a proxy that may be on localhost — hence --network host
# rather than the bridge DOCKER_RUN_PY uses.
#
# Only the non-secret endpoint URLs are read out of .env. Credentials are NEVER
# put on the docker command line: they are forwarded by name (`-e VAR`), so the
# value travels through the environment and appears in neither `ps` nor the
# recipe echo. The recipes are silenced for the same reason — make prints the
# command it is about to run, and that print is a log line.
BENCH_ARGS ?= --out bench-report.json
BENCH_URL_VARS = LLM_MAIN_BASE_URL LLM_FAST_BASE_URL LITELLM_BASE_URL
BENCH_SECRET_VARS = LITELLM_API_KEY LITELLM_MASTER_KEY REMOTE_LLM_KEY
BENCH_ENV = $(shell sed -n 's/^\(LLM_MAIN_BASE_URL\|LLM_FAST_BASE_URL\|LITELLM_BASE_URL\)=/-e \1=/p' .env 2>/dev/null)
# Anything exported or passed on the command line wins over .env: docker takes
# the last -e for a name. .env holds a developer's local endpoints; the deployed
# cluster answers on other ports with another key, and benchmarking it must not
# mean editing .env.
BENCH_OVERRIDE = $(foreach v,$(BENCH_URL_VARS),$(if $($(v)),-e $(v)=$($(v)))) \
                 $(foreach v,$(BENCH_SECRET_VARS),$(if $($(v)),-e $(v)))

# `-e VAR` forwards by name, which only works if the name is in the recipe's
# environment. A variable set on the make command line is not, unless exported —
# so `make bench LITELLM_API_KEY=…` would otherwise silently forward nothing.
export $(BENCH_SECRET_VARS)

bench:  ## serving latency/throughput benchmark (model/bench; needs the GPU tiers reachable)
	@docker run --rm --user 0 --network host -v "$(PWD):/src" -w /src \
	  -e PYTHONPATH=/src/backend:/src $(BENCH_ENV) $(BENCH_OVERRIDE) $(PY_IMAGE) \
	  python -m model.bench.run $(BENCH_ARGS)

GATE_ARGS ?= --report bench-report.json

bench-gate:  ## judge a benchmark report against model/bench/thresholds.json (0 pass / 1 fail / 2 inconclusive)
	$(DOCKER_RUN_PY) python -m model.bench.gate $(GATE_ARGS)

# The end-to-end harness needs a deployed agent and a credential, so nothing is
# read from .env: the operator exports what they already have, and every value
# is forwarded by name.
E2E_ARGS ?= --out e2e-report.json
E2E_VARS = BENCH_AGENT_URL BENCH_AGENT_ID BENCH_TENANT_ID BENCH_USER_ID BENCH_USER_ROLES \
           GATEWAY_KEY KB_GATEWAY_KEY
E2E_ENV = $(foreach v,$(E2E_VARS),$(if $($(v)),-e $(v)))

export $(E2E_VARS)

bench-e2e:  ## per-stage latency of one real question against a deployed agent (see model/bench/e2e.py)
	@docker run --rm --user 0 --network host -v "$(PWD):/src" -w /src \
	  -e PYTHONPATH=/src/backend:/src $(E2E_ENV) $(PY_IMAGE) \
	  python -m model.bench.e2e $(E2E_ARGS)

# The e2e suite (pytest -m e2e) drives the deployed V3 dev cluster through its LAN
# NodePorts (deploy/k8s/dev: Kong 30100, Keycloak 30110). It exercises api + worker-agent,
# so it needs the cluster up and seeded with seed_demo, not testcontainers. It
# logs in and runs real agent turns: every run writes conversations, so point it
# only at a cluster where that is acceptable. Override a URL to target another
# cluster. The account credentials (E2E_USER/E2E_PASS, E2E_ADMIN_*, E2E_T2_ADMIN_*,
# tests/README.md) are forwarded by name, like bench-e2e's secrets.
# The default inventory is k8s-local on OrbStack. Override these for the shared
# LAN cluster or another deployed environment.
E2E_KONG_URL ?= http://localhost:30100
E2E_KEYCLOAK_URL ?= http://localhost:30110
E2E_DATA_SPACE ?= DS-DEMO
E2E_TESTS ?= tests/integration/test_v3_question_e2e.py
E2E_FORWARD_VARS = E2E_USER E2E_PASS E2E_ADMIN_USER E2E_ADMIN_PASS E2E_T2_ADMIN_USER E2E_T2_ADMIN_PASS \
                   E2E_DATA_SPACE E2E_STRICT E2E_TURN_TIMEOUT
E2E_FORWARD = $(foreach v,$(E2E_FORWARD_VARS),$(if $($(v)),-e $(v)))

export $(E2E_FORWARD_VARS)

# --no-showlocals: a failing e2e test would otherwise print the passwords and tokens
# in its frames (pyproject sets --showlocals for every suite).
# The turn test writes its frames to tests/e2e/out/ as root; hand them back.
e2e:  ## pytest -m e2e against the deployed V3 dev cluster (LAN NodePorts; URLs overridable, credentials from env)
	@docker build -q --build-arg BASE=$(IMAGE_REGISTRY)/dataagent/worker-agent:latest --build-arg SUITE=worker_agent \
	  -f tests/test-image.Dockerfile -t dataagent-test/worker_agent:latest . >/dev/null
	@$(DOCKER_RUN_SUITE) --network host -e LIVE_STACK=1 -e LIVE_KONG_URL=$(E2E_KONG_URL) \
	  -e LIVE_KEYCLOAK_URL=$(E2E_KEYCLOAK_URL) $(E2E_FORWARD) \
	  dataagent-test/worker_agent:latest sh -c \
	  "python -m pytest -q -p no:cacheprovider --no-showlocals -m e2e $(PYTEST_ARGS) $(E2E_TESTS); \
	   rc=\$$?; chown -R $$(id -u):$$(id -g) tests/e2e/out 2>/dev/null; exit \$$rc"

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

deploy-local:  ## deploy the V3 local slice (api, worker-agent, web, migrate, gateway)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml \
		-e apply_jobs=true -e image_registry=$(IMAGE_REGISTRY) -e image_push=true \
		-e '{"deploy_image_allowlist":["api","worker-agent","migrate","frontend","kong","keycloak"],"service_manifests":["api","worker-agent","frontend","kong","keycloak"],"service_workloads":["api","worker-agent","frontend","kong","keycloak"]}'
