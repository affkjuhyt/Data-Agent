# One entry point for the commands that CI, the deploy playbook and the docs
# already run. Targets are thin wrappers: they must stay equivalent to what
# .github/workflows/ci.yml does, so a green `make ci` means a green pipeline.
SHELL := /bin/bash
.DEFAULT_GOAL := help
PY_IMAGE ?= localhost:5000/dataagent/workflow_runtime:latest
PLANTUML_IMAGE ?= plantuml/plantuml:1.2024.8
DOCKER_RUN_PY = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE)

.PHONY: help lint type test test-unit test-integration deadcode frontend-lint frontend-test ci e2e diagrams \
        deploy fmt bench bench-gate bench-e2e

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
               flow_builder:flow_builder model_service:model_service
INTEGRATION_SUITES ?= workflow_runtime:workflow_runtime auth_api:auth_api platform_workers:platform-workers \
                      sandbox_executor:sandbox_executor
# The integration sandbox tests start real sandbox-runner containers; the tag
# differs per host (tests/integration/_sandbox.py), override it to match.
SANDBOX_RUNNER_IMAGE ?= dataagent/sandbox-runner:latest
PYTEST_ARGS ?=
# --user 0 so pip can install the test tooling; no bytecode and no pytest cache,
# or the run leaves root-owned files in the checkout.
DOCKER_RUN_SUITE = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src \
                   -e PYTHONDONTWRITEBYTECODE=1 -e OTEL_SDK_DISABLED=true

# Every suite runs even when an earlier one fails; the target fails if any did.
test-unit:  ## pytest unit, one suite per service in that service's image
	@failed=""; for pair in $(UNIT_SUITES); do suite=$${pair%%:*}; image=$${pair##*:}; \
	  echo "== unit: $$suite ($(IMAGE_REGISTRY)/dataagent/$$image)"; \
	  $(DOCKER_RUN_SUITE) $(IMAGE_REGISTRY)/dataagent/$$image:latest sh -c \
	    "pip install -q -r requirements-test.txt && python -m pytest -q -p no:cacheprovider -m unit $(PYTEST_ARGS) tests/unit/$$suite" \
	    || failed="$$failed $$suite"; \
	done; if [ -n "$$failed" ]; then echo "failed unit suites:$$failed"; exit 1; fi

# Testcontainers talks to the host daemon through the mounted socket and reaches
# the containers it starts over the host network.
test-integration:  ## pytest integration (testcontainers), one suite per service image
	@failed=""; for pair in $(INTEGRATION_SUITES); do suite=$${pair%%:*}; image=$${pair##*:}; \
	  extra=""; [ -f tests/integration/$$suite/requirements.txt ] && extra="-r tests/integration/$$suite/requirements.txt"; \
	  echo "== integration: $$suite ($(IMAGE_REGISTRY)/dataagent/$$image)"; \
	  $(DOCKER_RUN_SUITE) --network host -v /var/run/docker.sock:/var/run/docker.sock \
	    -e TESTCONTAINERS_RYUK_DISABLED=true -e SANDBOX_RUNNER_IMAGE=$(SANDBOX_RUNNER_IMAGE) \
	    $(IMAGE_REGISTRY)/dataagent/$$image:latest sh -c \
	    "pip install -q -r requirements-integration.txt $$extra && python -m pytest -q -p no:cacheprovider -m integration $(PYTEST_ARGS) tests/integration/$$suite" \
	    || failed="$$failed $$suite"; \
	done; if [ -n "$$failed" ]; then echo "failed integration suites:$$failed"; exit 1; fi

test:  ## unit suites then integration suites (same split as CI python-test / python-integration)
	@$(MAKE) --no-print-directory test-unit && $(MAKE) --no-print-directory test-integration

deadcode:  ## vulture (python) + knip (frontend)
	$(DOCKER_RUN_PY) sh -c "pip install -q vulture && vulture"
	npm --workspace frontend run deadcode

frontend-lint:  ## tsc --noEmit
	npm --workspace frontend run typecheck

frontend-test:  ## vitest
	npm --workspace frontend run test

ci: lint type test frontend-lint frontend-test  ## everything CI runs, locally

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
# cluster. E2E_USER / E2E_PASS are forwarded by name, like bench-e2e's secrets.
E2E_KONG_URL ?= http://10.10.55.101:30100
E2E_KEYCLOAK_URL ?= http://10.10.55.101:30110
E2E_RUNTIME_URL ?= http://10.10.55.101:30240
E2E_TESTS ?= $(wildcard tests/integration/test_*.py)
E2E_FORWARD_VARS = E2E_USER E2E_PASS E2E_AGENT_ID E2E_STRICT E2E_TURN_TIMEOUT
E2E_FORWARD = $(foreach v,$(E2E_FORWARD_VARS),$(if $($(v)),-e $(v)))

export $(E2E_FORWARD_VARS)

# The turn test writes its frames to tests/e2e/out/ as root; hand them back.
e2e:  ## pytest -m e2e against the deployed dev cluster (LAN NodePorts; URLs overridable, credentials from env)
	@$(DOCKER_RUN_SUITE) --network host -e LIVE_STACK=1 -e LIVE_KONG_URL=$(E2E_KONG_URL) \
	  -e LIVE_KEYCLOAK_URL=$(E2E_KEYCLOAK_URL) -e WORKFLOW_RUNTIME_URL=$(E2E_RUNTIME_URL) $(E2E_FORWARD) \
	  $(IMAGE_REGISTRY)/dataagent/workflow_runtime:latest sh -c \
	  "pip install -q -r requirements-test.txt && python -m pytest -q -p no:cacheprovider -m e2e $(PYTEST_ARGS) $(E2E_TESTS); \
	   rc=\$$?; chown -R $$(id -u):$$(id -g) tests/e2e/out 2>/dev/null; exit \$$rc"

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

# HOME=/tmp + user.home=/tmp: the non-root uid has no passwd entry, so Java's user.home
# is "?" and it writes its font cache into the mounted diagrams dir as `?/.java/`, which
# then gets committed. HOME alone does not change user.home; the -D flag does.
diagrams:  ## render docs/v2/diagrams/*.puml to SVG (PlantUML pinned; see docs/v2/diagrams/README.md)
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -e JAVA_TOOL_OPTIONS=-Duser.home=/tmp -v "$(PWD)/docs/v2/diagrams:/data" -w /data $(PLANTUML_IMAGE) -tsvg -charset UTF-8 '*.puml'
