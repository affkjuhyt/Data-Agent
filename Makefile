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
	  backend/services/crawler backend/libs/sql_guard backend/libs/cron backend/libs/openbao backend/libs/vectors

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

# PL-15: every package the service images and the frontend ship, with its licence; fails on a
# forbidden one (tools/licenses.json). Scans the images last built (make deploy-local builds
# them), so rerun after a requirements change and commit THIRD-PARTY-NOTICES.md with it.
LICENSE_IMAGES = api worker-agent worker-ingest worker-document crawler ml-forecast python-sandbox
.PHONY: licenses tlc-reconcile tlc-cases tlc-documents golden-eval

licenses:  ## write THIRD-PARTY-NOTICES.md from the service images and the npm lock; fail on a forbidden licence
	@out=$$(mktemp -d); trap 'rm -rf "$$out"' EXIT; \
	for s in $(LICENSE_IMAGES); do \
	  docker run --rm --entrypoint python -v "$(PWD)/tools:/tools:ro" "$(IMAGE_REGISTRY)/dataagent/$${s}:latest" \
	    -I /tools/licenses.py scan --component "$$s" > "$$out/$$s.json" || exit 1; \
	done; \
	python3 tools/licenses.py npm --lock package-lock.json --component frontend > "$$out/frontend.json"; \
	python3 tools/licenses.py report "$$out"/*.json --notices THIRD-PARTY-NOTICES.md

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

# NYC TLC Trip Record Data, the real dataset of DS-TLC (tests/e2e/tlc, EV-01): the Parquet months,
# zone table and data dictionaries pinned by sha256 in sources.json, loaded as published (bad rows
# included) into their own database, readable by TLC_READER. Rerunning reloads it in one
# transaction; the cache keeps the ~170 MB download. nyc.gov terms: internal evaluation only.
TLC_CACHE ?= $(HOME)/.cache/dataagent/tlc
TLC_DATABASE ?= tlc_trips
TLC_READER ?= app_user
TLC_LOAD = docker run --rm -i -v "$(PWD)/tests/e2e/tlc:/tlc:ro" -v "$(TLC_CACHE):/cache" \
	$(DEV_TOOLS_IMAGE) python -I /tlc/load.py
.PHONY: tlc

tlc: dev-tools  ## load the NYC TLC trip snapshot into the cluster's PostgreSQL (TLC_DATABASE, TLC_READER)
	@mkdir -p "$(TLC_CACHE)" && $(TLC_LOAD) fetch --cache /cache
	@$(AW_PSQL) -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$(TLC_DATABASE)'" | grep -q 1 || \
	  $(AW_PSQL) -d postgres -c 'CREATE DATABASE "$(TLC_DATABASE)"'
	@$(AW_PSQL) -d postgres -c 'GRANT CONNECT ON DATABASE "$(TLC_DATABASE)" TO "$(TLC_READER)"'
	@$(TLC_LOAD) sql --cache /cache --reader "$(TLC_READER)" | $(AW_PSQL) -d "$(TLC_DATABASE)"
	@$(AW_PSQL) -d "$(TLC_DATABASE)" -tAc "SELECT source_file || ': ' || count(*) FROM (SELECT source_file FROM yellow_trip UNION ALL SELECT source_file FROM green_trip) t GROUP BY source_file ORDER BY source_file"

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

# PoC-1 / DI-01: the 30 bake-off documents labelled by two people in Label Studio
# (model/eval/docs/labelstudio/README.md). The documents and Label Studio's data stay on this
# machine, outside the repo (licences differ, scans may hold personal data); the repo keeps
# tests/data/docs-bakeoff/sources.json and the reconciled gold/. Label Studio listens on
# 127.0.0.1 only. The image is pinned by digest: 1.23.2, released 29/09/2026.
BAKEOFF_DOCS ?= $(HOME)/.cache/dataagent/docs-bakeoff
BAKEOFF_LABELS = $(PWD)/model/eval/docs/labelstudio
LABELSTUDIO_IMAGE ?= heartexlabs/label-studio:1.23.2@sha256:afcc516a22775a39d0d66f4c2ddc95d01be3e3d3862b21fcf4f9de34b4ad4e12
LABELSTUDIO_DATA ?= $(HOME)/.cache/dataagent/labelstudio
LABELSTUDIO_PORT ?= 8080
GOLD_OUT ?= $(BAKEOFF_DOCS)/gold-$(ANNOTATOR)
BAKEOFF_CONVERT = python -I /labels/convert.py
.PHONY: labelstudio labelstudio-stop bakeoff-render bakeoff-gold bakeoff-agree

labelstudio:  ## run Label Studio on 127.0.0.1:LABELSTUDIO_PORT over BAKEOFF_DOCS (bake-off labelling)
	@mkdir -p "$(LABELSTUDIO_DATA)" "$(BAKEOFF_DOCS)"
	@docker start dataagent-labelstudio >/dev/null 2>&1 || docker run -d --name dataagent-labelstudio \
	  -p 127.0.0.1:$(LABELSTUDIO_PORT):8080 \
	  -v "$(LABELSTUDIO_DATA):/label-studio/data" -v "$(BAKEOFF_DOCS):/label-studio/files/bakeoff:ro" \
	  -e LOCAL_FILES_SERVING_ENABLED=true -e LOCAL_FILES_DOCUMENT_ROOT=/label-studio/files \
	  $(LABELSTUDIO_IMAGE) >/dev/null
	@echo "Label Studio: http://127.0.0.1:$(LABELSTUDIO_PORT)"

labelstudio-stop:  ## stop Label Studio (its data stays in LABELSTUDIO_DATA)
	@docker stop dataagent-labelstudio >/dev/null && echo stopped

bakeoff-render: dev-tools  ## page images and Label Studio tasks of BAKEOFF_DOCS; update tests/data/docs-bakeoff/sources.json
	@docker run --rm -v "$(BAKEOFF_DOCS):/docs" -v "$(BAKEOFF_LABELS):/labels:ro" \
	  $(IMAGE_REGISTRY)/dataagent/worker-document:latest $(BAKEOFF_CONVERT) render --docs /docs
	@docker run --rm -v "$(BAKEOFF_DOCS):/docs:ro" -v "$(BAKEOFF_LABELS):/labels:ro" \
	  -v "$(PWD)/tests/data/docs-bakeoff:/out" $(DEV_TOOLS_IMAGE) \
	  $(BAKEOFF_CONVERT) manifest --docs /docs --out /out/sources.json

bakeoff-gold: dev-tools  ## one annotator's gold: EXPORT (under BAKEOFF_DOCS) ANNOTATOR=<id|email> [GOLD_OUT]
	@test -n "$(EXPORT)" -a -n "$(ANNOTATOR)" || { echo "EXPORT and ANNOTATOR are required"; exit 2; }
	@mkdir -p "$(GOLD_OUT)" && docker run --rm -v "$(BAKEOFF_DOCS):/docs:ro" -v "$(BAKEOFF_LABELS):/labels:ro" \
	  -v "$(GOLD_OUT):/out" $(DEV_TOOLS_IMAGE) \
	  $(BAKEOFF_CONVERT) gold --docs /docs --export "/docs/$(EXPORT)" --annotator "$(ANNOTATOR)" --out /out

bakeoff-agree: dev-tools  ## where annotators A and B differ (their gold-A, gold-B under BAKEOFF_DOCS)
	@test -n "$(A)" -a -n "$(B)" || { echo "A and B are required"; exit 2; }
	@docker run --rm -v "$(BAKEOFF_DOCS):/docs:ro" -v "$(BAKEOFF_LABELS):/labels:ro" $(DEV_TOOLS_IMAGE) \
	  $(BAKEOFF_CONVERT) agree --a "/docs/gold-$(A)" --b "/docs/gold-$(B)"

# EV-01: NYC 311 (erm2-nwe9) pulled into a fixed snapshot the real-data evaluation's gold answers
# are defined on (tests/e2e/nyc311/collect.py, docs/blueprint/DATASET_AND_EVALUATION.md §2). The rows
# stay in NYC311_CACHE (outside the repo: the terms are to be confirmed, the data is not shared);
# the manifest, row hashes and window, goes to model/eval/snapshots/. SOCRATA_APP_TOKEN from the
# environment (the portal throttles the shared pool without one). A pull with the same snapshot id
# that does not reproduce the manifest is refused: pull a new id.
NYC311_CACHE ?= $(HOME)/.cache/dataagent/nyc311
NYC311_SNAPSHOT ?= S3-2025Q1
NYC311_START ?= 2025-01-01
NYC311_END ?= 2025-04-01
NYC311_RUN = docker run --rm --user "$$(id -u):$$(id -g)" -v "$(PWD):/src" -w /src \
	-v "$(NYC311_CACHE):/cache" -e SOCRATA_APP_TOKEN $(DEV_TOOLS_IMAGE) python -I tests/e2e/nyc311/collect.py
.PHONY: nyc311-snapshot nyc311-drift

nyc311-snapshot: dev-tools  ## pull NYC 311 into a fixed snapshot (NYC311_SNAPSHOT, NYC311_START, NYC311_END)
	@mkdir -p "$(NYC311_CACHE)" && $(NYC311_RUN) pull --cache /cache --snapshot "$(NYC311_SNAPSHOT)" \
	  --start "$(NYC311_START)" --end "$(NYC311_END)"

nyc311-drift: dev-tools  ## count the NYC 311 rows the portal changed since a snapshot (NYC311_SNAPSHOT)
	@$(NYC311_RUN) drift --cache /cache --snapshot "$(NYC311_SNAPSHOT)"

# EV-02: SEC EDGAR reports of the twenty companies of tests/e2e/edgar/scope.json, FY2021-2024, as
# filed (inline XBRL, companyfacts, FSDS cut to their CIKs) into EDGAR_CACHE as Parquet, the
# manifest to model/eval/snapshots/ (docs/blueprint/DATASET_AND_EVALUATION.md §3). EDGAR's fair
# access policy wants a User-Agent naming the requester: EDGAR_USER_AGENT="<Company> <email>",
# from the environment. ~2 GB the first time (the FSDS quarters); a rerun reuses the cache.
# EDGAR_ARGS: --cik 320193,19617, --fy-from/--fy-to, --filed-to, --rate (<= 10), --snapshot.
EDGAR_CACHE ?= $(HOME)/.cache/dataagent/edgar
EDGAR_RUN = docker run --rm --user "$$(id -u):$$(id -g)" -v "$(PWD):/src" -w /src \
	-v "$(EDGAR_CACHE):/cache" -e EDGAR_USER_AGENT $(DEV_TOOLS_IMAGE) python -I tests/e2e/edgar/collect.py
.PHONY: edgar edgar-verify

edgar: dev-tools  ## pull the SEC EDGAR snapshot E20-FY21-24 (EDGAR_USER_AGENT, EDGAR_CACHE, EDGAR_ARGS)
	@test -n "$$EDGAR_USER_AGENT" || { echo "EDGAR_USER_AGENT='<Company> <email>' is required"; exit 2; }
	@mkdir -p "$(EDGAR_CACHE)" && $(EDGAR_RUN) --out /cache $(EDGAR_ARGS)

edgar-verify: dev-tools  ## HEAD the SEC snapshot's sources again and list what EDGAR changed
	@test -n "$$EDGAR_USER_AGENT" || { echo "EDGAR_USER_AGENT='<Company> <email>' is required"; exit 2; }
	@$(EDGAR_RUN) --verify $(EDGAR_ARGS)

# PL-04: the latest backups (RUNBOOK §9a) restored into throwaway pods of the current kubectl
# context and compared with the live stores; nothing live is written. Standard library + kubectl,
# so it runs on the host. RESTORE_DRILL_ARGS: --keep leaves the pods.
.PHONY: restore-drill

restore-drill:  ## restore the latest backups into throwaway pods and compare them with the live stores
	@python3 tests/ops/restore_drill.py $(RESTORE_DRILL_ARGS)

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

# EV-08: a golden eval set written as YAML (services/api/modules/eval/cases_yaml.py) loaded into
# the Data Space's eval set, in the api pod; a dry run unless EVAL_CASES_APPLY=1. Reloading
# updates the same items, retires the ones that left the file and reports a changed answer.
EVAL_CASES_ORG ?=
EVAL_CASES_SPACE ?=
EVAL_CASES_AS ?=
EVAL_CASES_FILE ?=
.PHONY: eval-cases-import

eval-cases-import:  ## load EVAL_CASES_FILE into EVAL_CASES_SPACE's eval set (dry run unless EVAL_CASES_APPLY=1)
	@kubectl -n dataagent-service exec -i deploy/api -- python -m services.api.modules.eval.cases_yaml \
		import - --org "$(EVAL_CASES_ORG)" --space "$(EVAL_CASES_SPACE)" --as "$(EVAL_CASES_AS)" \
		$(if $(filter 1,$(EVAL_CASES_APPLY)),,--dry-run) < "$(EVAL_CASES_FILE)"

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

# EV-03: DS-TLC's metrics (deploy/docker/semantic/ds-tlc.yaml), compiled by the real compiler, against
# the hand-written SQL of ds-tlc-reference.yaml, both run on the cluster's tlc_trips from the worker
# pod (worker_agent/semantic/reconcile.py). Reads only. The YAML is read into JSON by dev-tools.
RECONCILE_ORG ?=
RECONCILE_SPACE ?= DS-TLC
RECONCILE_MODEL ?= deploy/docker/semantic/ds-tlc.yaml
RECONCILE_REFERENCE ?= deploy/docker/semantic/ds-tlc-reference.yaml

tlc-reconcile:  ## DS-TLC metrics against hand-written SQL on the cluster's tlc_trips (RECONCILE_ORG)
	@docker run --rm -v "$(PWD):/src" -w /src $(DEV_TOOLS_IMAGE) python -c "import json, sys, yaml; \
		print(json.dumps({'model': yaml.safe_load(open(sys.argv[1])), 'reference': yaml.safe_load(open(sys.argv[2]))}, default=str))" \
		"$(RECONCILE_MODEL)" "$(RECONCILE_REFERENCE)" \
	| kubectl -n dataagent-service exec -i deploy/worker-agent -c worker-agent -- \
		python -m services.worker_agent.semantic.reconcile --org "$(RECONCILE_ORG)" --space "$(RECONCILE_SPACE)" -

# EV-05: the golden TLC cases' gold SQL run on the cluster's tlc_trips (reconcile --sql): their values
# and result hashes written into model/eval/cases/golden/tlc/*.jsonl (sql_run_on_snapshot), then
# deploy/docker/eval/ds-tlc-cases.yaml regenerated from them (tests/e2e/tlc/cases.py). Reads only.
tlc-cases:  ## run the golden TLC cases' SQL on tlc_trips, write their values and the eval YAML (RECONCILE_ORG)
	@docker run --rm -v "$(PWD):/src" -w /src $(DEV_TOOLS_IMAGE) python tests/e2e/tlc/cases.py export \
	| kubectl -n dataagent-service exec -i deploy/worker-agent -c worker-agent -- \
		python -m services.worker_agent.semantic.reconcile --org "$(RECONCILE_ORG)" --space "$(RECONCILE_SPACE)" --sql - \
	> .tlc-cases-results.json
	@docker run --rm -v "$(PWD):/src" -w /src $(DEV_TOOLS_IMAGE) sh -c \
		"python tests/e2e/tlc/cases.py apply .tlc-cases-results.json && python tests/e2e/tlc/cases.py yaml"
	@rm -f .tlc-cases-results.json

# EV-05: the TLC data dictionaries (pinned in tests/e2e/tlc/sources.json, cached by `make tlc`; their
# terms keep them out of the repo) uploaded to DS-TLC's documents through the upload route's own code,
# in the api pod (api/modules/document/import_files.py); worker-document then reads and indexes them.
TLC_DOCUMENTS_AS ?= helios-admin
TLC_DICTIONARIES = data_dictionary_trip_records_yellow.pdf data_dictionary_trip_records_green.pdf

tlc-documents:  ## upload the cached TLC data dictionaries to DS-TLC's documents (RECONCILE_ORG, TLC_DOCUMENTS_AS)
	@pod=$$(kubectl -n dataagent-service get pods -l app=api -o jsonpath='{.items[0].metadata.name}'); \
	for f in $(TLC_DICTIONARIES); do kubectl -n dataagent-service cp "$(TLC_CACHE)/$$f" "$$pod:/tmp/$$f" -c api; done; \
	kubectl -n dataagent-service exec $$pod -c api -- python -m services.api.modules.document.import_files \
		$(addprefix /tmp/,$(TLC_DICTIONARIES)) --org "$(RECONCILE_ORG)" --space DS-TLC --as "$(TLC_DOCUMENTS_AS)" \
		--collection tlc-dictionaries --name "Từ điển dữ liệu TLC" \
		--purpose "Định nghĩa cột, mã và quy tắc của dữ liệu chuyến taxi vàng và xanh"
# The copies stay in the api pod's /tmp, an emptyDir that goes with the pod.

# EV-09: the golden cases of a dataset (model/eval/cases/golden/<GOLDEN_SET>/) asked through the
# real POST /v1/questions on the cluster's NodePorts, as a person: EVAL_USER/EVAL_PASS (a password
# grant on the frontend client) or EVAL_ACCESS_TOKEN, forwarded by name. Runs in the worker-agent
# test image (the scorers import the worker's comparison and libs.language). Writes what asking
# writes: a conversation, question and answer per case. GOLDEN_ARGS: --case ID (repeatable),
# --limit N, --no-judge, --data-space CODE. The judge(...) cases need LITELLM_API_KEY.
GOLDEN_SET ?= tlc
GOLDEN_OUT ?= golden-report.json
GOLDEN_FORWARD = $(foreach v,EVAL_USER EVAL_PASS EVAL_ACCESS_TOKEN LITELLM_API_KEY,$(if $($(v)),-e $(v)))
golden-eval:  ## EV-09 golden tier: the golden cases through POST /v1/questions (EVAL_USER/EVAL_PASS; GOLDEN_SET, GOLDEN_ARGS)
	@docker build -q --build-arg BASE=$(IMAGE_REGISTRY)/dataagent/worker-agent:latest --build-arg SUITE=worker_agent \
	  -f tests/test-image.Dockerfile -t dataagent-test/worker_agent:latest . >/dev/null
	@$(DOCKER_RUN_SUITE) --network host $(GOLDEN_FORWARD) -e LITELLM_BASE_URL=http://localhost:30500 \
	  dataagent-test/worker_agent:latest python -m model.eval.run --tier golden --golden $(GOLDEN_SET) \
	  --agent-url $(E2E_KONG_URL) --keycloak-url $(E2E_KEYCLOAK_URL) --out $(GOLDEN_OUT) $(GOLDEN_ARGS)

agent-eval:  ## the dynamic agent on a Data Space's eval set (AGENT_EVAL_ORG, AGENT_EVAL_AS; AGENT_EVAL_ARGS)
	@kubectl -n dataagent-service exec -i deploy/worker-agent -c worker-agent -- \
		python -m services.worker_agent.agentic.evaluate --org "$(AGENT_EVAL_ORG)" \
		--space "$(AGENT_EVAL_SPACE)" --as "$(AGENT_EVAL_AS)" $(AGENT_EVAL_ARGS)

# EV-19: how well a producer's confidence says how often it is right (model/eval/calibrate.py,
# docs/blueprint/CONFIDENCE_SPEC.md), from a report of the two targets above, read on stdin:
# sqlgen_vote from `make sqlgen-ablation ABLATION_ARGS="--variants all --json"`, understanding
# from `make agent-eval AGENT_EVAL_ARGS="--engines governed --json"`. Standard library only, in
# the api image like bench-gate; `--json` or `--write <path>` go in CALIBRATE_ARGS. AE-23: with an
# ablation of all four variants, `--revote` counts the vote by way of writing again on the same
# candidates and compares it with the count vote; `--write-weights <path>` writes the ways' weights
# (backend/libs/calibration/maps/sqlgen_vote.json, read by the worker).
CALIBRATE_PRODUCER ?= sqlgen_vote
CALIBRATE_FROM ?=
CALIBRATE_ARGS ?=
.PHONY: calibrate

calibrate:  ## ECE, Brier, AURC and bars of a producer's confidence (CALIBRATE_PRODUCER, CALIBRATE_FROM)
	@test -n "$(CALIBRATE_FROM)" || { echo "CALIBRATE_FROM=<report.json> is required"; exit 2; }
	@docker run --rm -i --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE) \
		python -m model.eval.calibrate --producer "$(CALIBRATE_PRODUCER)" \
		--commit "$$(git rev-parse --short HEAD)" $(CALIBRATE_ARGS) < "$(CALIBRATE_FROM)"

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

LOCAL_DATABASES = ["postgres","pgbouncer","redis","minio","kafka","openbao","qdrant","clickhouse"]
LOCAL_DATABASE_WORKLOADS = ["redis","minio","kafka","openbao","qdrant","clickhouse"]

deploy-local:  ## deploy the V3 local slice (api, worker-agent, worker-ingest, worker-document, python-sandbox, ml-forecast, crawler, egress-proxy, temporal, web, migrate, gateway, litellm, mailpit)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml \
		-e apply_jobs=true -e image_registry=$(IMAGE_REGISTRY) -e image_push=true \
		-e '{"database_manifests":$(LOCAL_DATABASES),"database_workloads":$(LOCAL_DATABASE_WORKLOADS)}' \
		-e '{"deploy_image_allowlist":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","migrate","frontend","kong","keycloak"],"service_manifests":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","egress-proxy","temporal","frontend","kong","keycloak","prometheus","litellm","mailpit"],"service_workloads":["api","worker-agent","worker-ingest","worker-document","python-sandbox","ml-forecast","crawler","egress-proxy","temporal","temporal-ui","frontend","kong","keycloak","prometheus","litellm","mailpit"]}'
