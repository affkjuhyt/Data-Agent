# One entry point for the commands that CI, the deploy playbook and the docs
# already run. Targets are thin wrappers: they must stay equivalent to what
# .github/workflows/ci.yml does, so a green `make ci` means a green pipeline.
SHELL := /bin/bash
.DEFAULT_GOAL := help
PY_IMAGE ?= localhost:5000/dataagent/workflow_runtime:latest
PLANTUML_IMAGE ?= plantuml/plantuml:1.2024.8
DOCKER_RUN_PY = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE)

.PHONY: help lint type test test-unit test-integration test-changed deadcode frontend-lint frontend-test ci e2e diagrams \
        deploy fmt bench bench-gate bench-e2e mock-api contract-compat

help:  ## List targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

lint:  ## ruff lint + format check (same as CI python-lint)
	$(DOCKER_RUN_PY) sh -c "pip install -q ruff==0.6.9 && ruff check . && ruff format --check ."

fmt:  ## ruff format + autofix
	$(DOCKER_RUN_PY) sh -c "pip install -q ruff==0.6.9 && ruff check --fix . && ruff format ."

type:  ## mypy strict on libs + services (same set as CI python-type)
	$(DOCKER_RUN_PY) sh -c "pip install -q mypy && mypy backend/libs backend/services"

# One suite per service, each run in that service's own image: no single image
# can import every service (their OTel/boto3 pins conflict). suite:image pairs;
# tests/unit/<suite> and tests/integration/<suite> are the directories.
IMAGE_REGISTRY ?= localhost:5000
UNIT_SUITES ?= workflow_runtime:workflow_runtime kb_backend:kb-backend platform_workers:platform-workers \
               sandbox_executor:sandbox_executor auth_api:auth_api kb_worker:kb-worker \
               model_service:model_service contracts:kb-backend indicator_api:indicator-api
INTEGRATION_SUITES ?= workflow_runtime:workflow_runtime auth_api:auth_api platform_workers:platform-workers \
                      sandbox_executor:sandbox_executor contracts:kb-backend
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

deadcode:  ## vulture (python) + knip (frontend)
	$(DOCKER_RUN_PY) sh -c "pip install -q vulture && vulture"
	npm --workspace frontend run deadcode

frontend-lint:  ## tsc --noEmit
	npm --workspace frontend run typecheck

frontend-test:  ## vitest
	npm --workspace frontend run test

ci: lint type test frontend-lint frontend-test contract-compat  ## everything CI runs, locally

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

# The e2e suite (pytest -m e2e) drives the deployed dev cluster through its LAN
# NodePorts (deploy/k8s/dev: Kong 30100, Keycloak 30110, workflow-runtime 30240),
# so it needs the cluster up and seeded with seed_demo, not testcontainers. It
# logs in and runs real agent turns: every run writes conversations, so point it
# only at a cluster where that is acceptable. Override a URL to target another
# cluster. The account credentials (E2E_USER/E2E_PASS, E2E_ADMIN_*, E2E_T2_ADMIN_*,
# tests/README.md) are forwarded by name, like bench-e2e's secrets.
E2E_KONG_URL ?= http://10.10.55.101:30100
E2E_KEYCLOAK_URL ?= http://10.10.55.101:30110
E2E_RUNTIME_URL ?= http://10.10.55.101:30240
E2E_TESTS ?= $(wildcard tests/integration/test_*.py)
E2E_FORWARD_VARS = E2E_USER E2E_PASS E2E_ADMIN_USER E2E_ADMIN_PASS E2E_T2_ADMIN_USER E2E_T2_ADMIN_PASS \
                   E2E_AGENT_ID E2E_STRICT E2E_TURN_TIMEOUT
E2E_FORWARD = $(foreach v,$(E2E_FORWARD_VARS),$(if $($(v)),-e $(v)))

export $(E2E_FORWARD_VARS)

# --no-showlocals: a failing e2e test would otherwise print the passwords and tokens
# in its frames (pyproject sets --showlocals for every suite).
# The turn test writes its frames to tests/e2e/out/ as root; hand them back.
e2e:  ## pytest -m e2e against the deployed dev cluster (LAN NodePorts; URLs overridable, credentials from env)
	@docker build -q --build-arg BASE=$(IMAGE_REGISTRY)/dataagent/workflow_runtime:latest --build-arg SUITE=workflow_runtime \
	  -f tests/test-image.Dockerfile -t dataagent-test/workflow_runtime:latest . >/dev/null
	@$(DOCKER_RUN_SUITE) --network host -e LIVE_STACK=1 -e LIVE_KONG_URL=$(E2E_KONG_URL) \
	  -e LIVE_KEYCLOAK_URL=$(E2E_KEYCLOAK_URL) -e WORKFLOW_RUNTIME_URL=$(E2E_RUNTIME_URL) $(E2E_FORWARD) \
	  dataagent-test/workflow_runtime:latest sh -c \
	  "python -m pytest -q -p no:cacheprovider --no-showlocals -m e2e $(PYTEST_ARGS) $(E2E_TESTS); \
	   rc=\$$?; chown -R $$(id -u):$$(id -g) tests/e2e/out 2>/dev/null; exit \$$rc"

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

# HOME=/tmp + user.home=/tmp: the non-root uid has no passwd entry, so Java's user.home
# is "?" and it writes its font cache into the mounted diagrams dir as `?/.java/`, which
# then gets committed. HOME alone does not change user.home; the -D flag does.
diagrams:  ## render docs/v2/diagrams/*.puml to SVG (PlantUML pinned; see docs/v2/diagrams/README.md)
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -e JAVA_TOOL_OPTIONS=-Duser.home=/tmp -v "$(PWD)/docs/v2/diagrams:/data" -w /data $(PLANTUML_IMAGE) -tsvg -charset UTF-8 '*.puml'
