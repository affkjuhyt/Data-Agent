# syntax=docker/dockerfile:1
# Lint and type tooling, built once and cached. `make lint`, `fmt`, `type-v3` and
# `lint-imports` used to `pip install` ruff / mypy into a fresh container on every run;
# from a slow PyPI mirror that took minutes for a tool that runs in a second.
# Rebuilt only when this file or a requirements file it copies changes.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
COPY backend/services/api/requirements.txt /tmp/reqs/api.txt
COPY backend/services/worker_agent/requirements.txt /tmp/reqs/worker_agent.txt
COPY backend/services/crawler/requirements.txt /tmp/reqs/crawler.txt
# Pins match pyproject.toml (ruff, mypy) and CI. The V3 services' own requirements are here
# because strict mypy needs FastAPI, Pydantic and SQLAlchemy installed to type their results.
# It is also the base of the `repo` unit suite (tests/unit/repo: manifests, Ansible, model/),
# which reads YAML (PyYAML pinned here rather than left to arrive through uvicorn[standard])
# and runs model/eval's policy scorer over sandbox_executor's sql_guard (its sqlparse pin).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -q ruff==0.6.9 mypy==2.4.0 import-linter==2.1 pyyaml==6.0.3 sqlparse==0.5.1 \
      -r /tmp/reqs/api.txt -r /tmp/reqs/worker_agent.txt -r /tmp/reqs/crawler.txt
