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

.PHONY: help lint type test test-unit test-integration test-changed test-related deadcode frontend-lint frontend-test ci e2e \
        deploy deploy-local fmt bench bench-gate  design-checklist design-audit api-types dev-tools fmt-files \
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
	  --ignore-missing-imports --follow-imports=silent backend/services/api backend/services/worker_agent \
	  backend/services/crawler backend/libs/sql_guard backend/libs/cron backend/libs/openbao

lint-imports: dev-tools  ## import-linter contracts (.importlinter), as CI
	docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend $(DEV_TOOLS_IMAGE) lint-imports

type:  ## mypy strict on libs + services (same set as CI python-type)
	$(DOCKER_RUN_PY) sh -c "pip install -q mypy==2.4.0 && mypy backend/libs backend/services"

# One suite per service, each run in that service's own image: no single image
# can import every service (their OTel/boto3 pins conflict). suite:image pairs;
# tests/unit/<suite> and tests/integration/<suite> are the directories. `repo` (deploy/,
# k8s, Ansible, model/, cross-service policy) belongs to no service and runs on the local
# dev-tools image: an image naming a `/` is used as given, not looked up in the registry.
IMAGE_REGISTRY ?= localhost:5001
UNIT_SUITES ?= contracts:api api:api worker_agent:worker-agent worker_ingest:worker-ingest \
               worker_document:worker-document crawler:crawler ml_forecast:ml-forecast repo:$(DEV_TOOLS_IMAGE)
INTEGRATION_SUITES ?= api:api migrate:migrate worker_agent:worker-agent worker_ingest:worker-ingest \
                      worker_document:worker-document crawler:crawler
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
             IMAGE_REGISTRY="$(IMAGE_REGISTRY)" \
             PYTEST_ARGS="$(PYTEST_ARGS)" python3 tests/run_suites.py --jobs $(TEST_JOBS)

# dev-tools first: the repo suite's base image. A cache hit after the first build.
test-unit: dev-tools  ## pytest unit, one suite per service in that service's image, in parallel
	@$(RUN_SUITES) unit

test-integration:  ## pytest integration (testcontainers), one suite per service image, in parallel
	@$(RUN_SUITES) integration

test: dev-tools  ## unit + integration suites, all at once (same suites as CI python-test / python-integration)
	@$(RUN_SUITES) all

test-changed: dev-tools  ## only the suites the changes since CHANGED_BASE (default main) can affect
	@$(RUN_SUITES) changed --base $(CHANGED_BASE)

test-related: dev-tools  ## only the test files the changes since CHANGED_BASE touch (tests/README.md)
	@$(RUN_SUITES) related --base $(CHANGED_BASE)

# Tab 7 F3: the frontend's types come from the api's own OpenAPI (ADR-48), never written by hand.
# The api image has the dependencies; backend/ is mounted so the dump reflects the working tree.
api-types:  ## dump the api OpenAPI to frontend/src/lib/v1/openapi.json and generate schema.ts from it (scripts/api-types.mjs)
	docker run --rm -v "$(PWD)/backend:/app:ro" -e API_OIDC_ISSUER=http://unused $(IMAGE_REGISTRY)/dataagent/api:latest \
	  python -c "import json, sys; from services.api.app import app; json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)" \
	  > frontend/src/lib/v1/openapi.json
	node frontend/scripts/api-types.mjs

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

ci: lint type test frontend-lint frontend-test design-audit  ## everything CI runs, locally

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

# The e2e suite (pytest -m e2e) drives the deployed V3 dev cluster through its LAN
# NodePorts (deploy/k8s/dev: Kong 30100, Keycloak 30110). It exercises api + worker-agent,
# so it needs the cluster up and seeded with seed_demo, not testcontainers. It
# logs in and runs real agent turns: every run writes conversations, so point it
# only at a cluster where that is acceptable. Override a URL to target another
# cluster. The account credentials (E2E_USER/E2E_PASS, E2E_ADMIN_*, E2E_T2_ADMIN_*,
# tests/README.md) are forwarded by name.
# The default inventory is k8s-local on OrbStack. Override these for the shared
# LAN cluster or another deployed environment.
E2E_KONG_URL ?= http://localhost:30100
E2E_KEYCLOAK_URL ?= http://localhost:30110
E2E_DATA_SPACE ?= DS-AW
E2E_TESTS ?= tests/integration/test_v3_question_e2e.py
E2E_FORWARD_VARS = E2E_USER E2E_PASS E2E_ADMIN_USER E2E_ADMIN_PASS E2E_T2_ADMIN_USER E2E_T2_ADMIN_PASS \
                   E2E_VIEWER_USER E2E_VIEWER_PASS E2E_DATA_SPACE E2E_TURN_TIMEOUT
E2E_FORWARD = $(foreach v,$(E2E_FORWARD_VARS),$(if $($(v)),-e $(v)))

export $(E2E_FORWARD_VARS)

# --no-showlocals: a failing e2e test would otherwise print the passwords and tokens
# in its frames (pyproject sets --showlocals for every suite).
e2e:  ## pytest -m e2e against the deployed V3 dev cluster (LAN NodePorts; URLs overridable, credentials from env)
	@docker build -q --build-arg BASE=$(IMAGE_REGISTRY)/dataagent/worker-agent:latest --build-arg SUITE=worker_agent \
	  -f tests/test-image.Dockerfile -t dataagent-test/worker_agent:latest . >/dev/null
	@$(DOCKER_RUN_SUITE) --network host -e LIVE_STACK=1 -e LIVE_KONG_URL=$(E2E_KONG_URL) \
	  -e LIVE_KEYCLOAK_URL=$(E2E_KEYCLOAK_URL) $(E2E_FORWARD) \
	  dataagent-test/worker_agent:latest sh -c \
	  "python -m pytest -q -p no:cacheprovider --no-showlocals -m e2e $(PYTEST_ARGS) $(E2E_TESTS)"

# Screen audit: every ROUTE_MAP page's primary GETs as helios-engineer and helios-viewer.
# Writes tests/screen_audit/out/screen_audit.tsv (labels: content/empty/access_*/server_error/…).
# Needs seed_demo (incl. helios-viewer) and E2E_PASS + E2E_VIEWER_PASS. See tests/README.md.
screen-audit:  ## probe all UI screens against the cluster; write screen_audit.tsv (does not fail on empty)
	@$(MAKE) e2e E2E_TESTS=tests/integration/test_screen_audit_e2e.py PYTEST_ARGS="-v -s $(PYTEST_ARGS)"

# AdventureWorksDW, the dataset the product is tested on (tests/e2e/adventureworks): fetched at the
# commit pinned in sources.json, converted to PostgreSQL and loaded into its own database of the
# cluster, readable by AW_READER, the login of the demo source (OpenBao demo-postgres). Rerunning
# reloads it; the cache keeps the download.
AW_CACHE ?= $(HOME)/.cache/dataagent/adventureworks
AW_DATABASE ?= adventureworks_dw
AW_READER ?= app_user
AW_PSQL ?= kubectl -n dataagent-database exec -i postgres-0 -- psql -U admin -v ON_ERROR_STOP=1 -q
AW_LOAD = docker run --rm -i -v "$(PWD)/tests/e2e/adventureworks:/aw:ro" -v "$(AW_CACHE):/cache" \
	$(DEV_TOOLS_IMAGE) python -I /aw/load.py
.PHONY: adventureworks

adventureworks: dev-tools  ## load AdventureWorksDW into the cluster's PostgreSQL (AW_DATABASE, AW_READER)
	@mkdir -p "$(AW_CACHE)" && $(AW_LOAD) fetch --cache /cache
	@$(AW_PSQL) -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$(AW_DATABASE)'" | grep -q 1 || \
	  $(AW_PSQL) -d postgres -c 'CREATE DATABASE "$(AW_DATABASE)"'
	@$(AW_PSQL) -d postgres -c 'GRANT CONNECT ON DATABASE "$(AW_DATABASE)" TO "$(AW_READER)"'
	@$(AW_LOAD) sql --cache /cache --reader "$(AW_READER)" | $(AW_PSQL) -d "$(AW_DATABASE)"
	@$(AW_PSQL) -d "$(AW_DATABASE)" -tAc "SELECT 'fact_internet_sales rows: ' || count(*) FROM fact_internet_sales"

# The document corpus of DS-AW (tests/e2e/adventureworks/documents.py): the files of
# deploy/docker/adventureworks-docs/ read by worker-document's own extractor and chunker, written as
# the db-init seed. Rerun after editing a file or the pipeline; a stale seed fails the unit tests.
AW_DOCUMENTS_SEED = deploy/docker/sql/v3-adventureworks-documents.sql
.PHONY: adventureworks-documents

adventureworks-documents:  ## regenerate the DS-AW document seed from deploy/docker/adventureworks-docs/
	@docker run --rm -v "$(PWD):/src:ro" -w /src -e PYTHONPATH=/src/backend:/src \
	  -e PYTHONDONTWRITEBYTECODE=1 $(IMAGE_REGISTRY)/dataagent/worker-document:latest \
	  python tests/e2e/adventureworks/documents.py sql > $(AW_DOCUMENTS_SEED).tmp \
	  && mv $(AW_DOCUMENTS_SEED).tmp $(AW_DOCUMENTS_SEED)

# R1-06: a Data Space's semantic model as YAML, read and written in the api pod with its
# svc_context credentials. SEMANTIC_ORG is the organisation's id; an import is a dry run unless
# SEMANTIC_APPLY=1, and approves nothing (relationships and metric versions wait for review).
SEMANTIC_ORG ?=
SEMANTIC_SPACE ?=
SEMANTIC_AS ?=
SEMANTIC_FILE ?= semantic-model.yaml
SEMANTIC_YAML = kubectl -n dataagent-service exec -i deploy/api -- \
	python -m services.api.modules.context.semantic_yaml
.PHONY: semantic-export semantic-import

semantic-export:  ## a Data Space's semantic model as YAML on stdout (SEMANTIC_ORG, SEMANTIC_SPACE)
	@$(SEMANTIC_YAML) export --org "$(SEMANTIC_ORG)" --space "$(SEMANTIC_SPACE)"

semantic-import:  ## import SEMANTIC_FILE as SEMANTIC_AS (dry run unless SEMANTIC_APPLY=1)
	@$(SEMANTIC_YAML) import - --org "$(SEMANTIC_ORG)" --space "$(SEMANTIC_SPACE)" \
		--as "$(SEMANTIC_AS)" $(if $(filter 1,$(SEMANTIC_APPLY)),,--dry-run) < "$(SEMANTIC_FILE)"

# Document retrieval scored against gold passages, in the worker-agent pod so it uses the
# cluster's vector index and reranker; the report says which of them ran (RG-02).
RETRIEVAL_ORG ?=
RETRIEVAL_SPACE ?= DS-AW
RETRIEVAL_CASES ?= model/eval/cases/retrieval.jsonl
.PHONY: eval-retrieval

eval-retrieval:  ## recall@k, MRR and not-found accuracy of document retrieval (RETRIEVAL_ORG, RETRIEVAL_SPACE)
	@kubectl -n dataagent-service exec -i deploy/worker-agent -- \
		python -m services.worker_agent.documents.retrieval_eval \
		--org "$(RETRIEVAL_ORG)" --space "$(RETRIEVAL_SPACE)" - < "$(RETRIEVAL_CASES)"

# R1-09: what each way of writing SQL earns on a Data Space's eval set, with the served model, in
# the worker pod (worker_agent/sqlgen/ablation.py). Reads as ABLATION_AS; writes only query runs.
ABLATION_ORG ?=
ABLATION_SPACE ?= DS-AW
AGENT_EVAL_SPACE ?= DS-AW
ABLATION_AS ?=
ABLATION_SET ?= Câu hỏi AdventureWorks
ABLATION_ARGS ?=
.PHONY: sqlgen-ablation

sqlgen-ablation:  ## SQL expert ablation on ABLATION_SET (ABLATION_ORG, ABLATION_SPACE, ABLATION_AS)
	@kubectl -n dataagent-service exec -i deploy/worker-agent -- \
		python -m services.worker_agent.sqlgen.ablation --org "$(ABLATION_ORG)" \
		--space "$(ABLATION_SPACE)" --as "$(ABLATION_AS)" --set "$(ABLATION_SET)" $(ABLATION_ARGS)

agent-eval:  ## the dynamic agent on a Data Space's eval set (AGENT_EVAL_ORG, AGENT_EVAL_AS; AGENT_EVAL_ARGS)
	@kubectl -n dataagent-service exec -i deploy/worker-agent -c worker-agent -- \
		python -m services.worker_agent.agentic.evaluate --org "$(AGENT_EVAL_ORG)" \
		--space "$(AGENT_EVAL_SPACE)" --as "$(AGENT_EVAL_AS)" $(AGENT_EVAL_ARGS)

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

LOCAL_DATABASES = ["postgres","pgbouncer","redis","minio","kafka","openbao","qdrant","clickhouse"]
LOCAL_DATABASE_WORKLOADS = ["redis","minio","kafka","openbao","qdrant","clickhouse"]

deploy-local:  ## deploy the V3 local slice (api, worker-agent, worker-ingest, worker-document, python-sandbox, ml-forecast, crawler, egress-proxy, temporal, web, migrate, gateway, litellm, mailpit)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml \
		-e apply_jobs=true -e image_registry=$(IMAGE_REGISTRY) -e image_push=true \
		-e '{"database_manifests":$(LOCAL_DATABASES),"database_workloads":$(LOCAL_DATABASE_WORKLOADS)}' \
		-e '{"deploy_image_allowlist":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","migrate","frontend","kong","keycloak"],"service_manifests":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","egress-proxy","temporal","frontend","kong","keycloak","prometheus","litellm","mailpit"],"service_workloads":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","egress-proxy","temporal","temporal-ui","frontend","kong","keycloak","prometheus","litellm","mailpit"]}'
