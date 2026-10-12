# syntax=docker/dockerfile:1
# Image for the ONLINE evaluation tier — the one that needs a model.
#
# The offline tier deliberately does not need this: it is pure Python over the
# real guards and the real metric compiler, so CI installs sqlparse and runs
# `python -m model.eval.run --tier offline` directly. Building a 1GB image to
# gate a pull request would be the reason nobody gates pull requests.
#
# The service trees are copied rather than pip-installed because the scorers
# import the REAL modules — sql_guard, security_visitor, the planner node, the
# shipped responder prompt. An eval that vendored its own copies would grade
# the copies.
FROM python:3.11-slim

WORKDIR /app

COPY model/eval/requirements.txt ./eval-requirements.txt
# ragas + judge model client, then the worker-agent's shared V3 dependencies.
RUN --mount=type=cache,target=/root/.cache/pip pip install -r eval-requirements.txt \
    && pip install -r backend/services/worker_agent/requirements.txt \
    && pip install sqlparse

# Same layout as the checkout (backend/…) so model/eval/_paths.py resolves
# the service trees identically in CI and in this image.
COPY model ./model
COPY backend/libs ./backend/libs
COPY backend/services/sandbox_executor ./backend/services/sandbox_executor
COPY backend/services/worker_agent ./backend/services/worker_agent

# Runs everything and gates on the result: a report with no verdict is a
# dashboard, and this container exists to answer yes or no.
