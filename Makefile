# One entry point for the commands that CI, the deploy playbook and the docs
# already run. Targets are thin wrappers: they must stay equivalent to what
# .github/workflows/ci.yml does, so a green `make ci` means a green pipeline.
SHELL := /bin/bash
.DEFAULT_GOAL := help
PY_IMAGE ?= localhost:5000/dataagent/workflow_runtime:latest
PLANTUML_IMAGE ?= plantuml/plantuml:1.2024.8
DOCKER_RUN_PY = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE)

.PHONY: help lint type test test-unit deadcode frontend-lint frontend-test ci e2e diagrams deploy fmt \
        bench bench-gate bench-e2e

help:  ## List targets
	@grep -hE '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

lint:  ## ruff lint + format check (same as CI python-lint)
	$(DOCKER_RUN_PY) sh -c "pip install -q ruff==0.6.9 && ruff check . && ruff format --check ."

fmt:  ## ruff format + autofix
	$(DOCKER_RUN_PY) sh -c "pip install -q ruff==0.6.9 && ruff check --fix . && ruff format ."

type:  ## mypy strict on libs + services (same set as CI python-type)
	$(DOCKER_RUN_PY) sh -c "pip install -q mypy && mypy backend/libs backend/services"

test-unit:  ## pytest unit only
	$(DOCKER_RUN_PY) sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q -o addopts='' tests/unit"

test:  ## pytest unit + integration with coverage (same as CI python-test)
	$(DOCKER_RUN_PY) sh -c "pip install -q pytest pytest-asyncio pytest-cov && python -m pytest -m 'unit or integration' --cov"

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
# the last -e for a name. .env describes the compose stack; the deployed cluster
# answers on other ports with another key, and benchmarking it must not mean
# editing .env.
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

e2e:  ## local end-to-end stack + suite (docker compose)
	bash tests/e2e/e2e.sh

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

# HOME=/tmp: without it the non-root user has no home, so Java writes its font
# cache into the mounted diagrams dir as `?/.java/` and it gets committed.
diagrams:  ## render the archived v1 PlantUML diagrams to SVG (version pinned; see docs/archive/v1-ankt/README.md)
	docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$(PWD)/docs/archive/v1-ankt/diagrams:/data" -w /data $(PLANTUML_IMAGE) -tsvg -charset UTF-8 '*.puml'
