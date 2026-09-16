# One entry point for the commands that CI, the deploy playbook and the docs
# already run. Targets are thin wrappers: they must stay equivalent to what
# .github/workflows/ci.yml does, so a green `make ci` means a green pipeline.
SHELL := /bin/bash
.DEFAULT_GOAL := help
PY_IMAGE ?= localhost:5000/dataagent/workflow_runtime:latest
PLANTUML_IMAGE ?= plantuml/plantuml:1.2024.8
DOCKER_RUN_PY = docker run --rm --user 0 -v "$(PWD):/src" -w /src -e PYTHONPATH=/src/backend:/src $(PY_IMAGE)

.PHONY: help lint type test test-unit deadcode frontend-lint frontend-test ci e2e diagrams deploy fmt

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

e2e:  ## local end-to-end stack + suite (docker compose)
	bash tests/e2e/e2e.sh

deploy:  ## deploy to the k8s-local inventory (Ansible is the only deploy path)
	cd deploy/ansible && ansible-playbook -i inventories/k8s-local deploy.yml

diagrams:  ## render docs/ankt PlantUML diagrams to SVG (version pinned; see docs/ankt/README.md)
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(PWD)/docs/ankt/diagrams:/data" -w /data $(PLANTUML_IMAGE) -tsvg -charset UTF-8 '*.puml'
