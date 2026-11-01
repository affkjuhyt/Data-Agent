# syntax=docker/dockerfile:1
# The `worker-agent` block of ADR-28. Python 3.12 per ADR-29. Build context is the repo root.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
# Before any COPY: a code change then reuses this layer instead of rerunning it.
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/worker_agent/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
# The V3 ORM and the wire contracts (Plan IR, coded refusals) are the only libs it needs.
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/libs/sql_guard/ ./libs/sql_guard/
COPY backend/libs/cron/ ./libs/cron/
COPY backend/libs/clock/ ./libs/clock/
COPY backend/libs/language/ ./libs/language/
COPY backend/libs/tabular/ ./libs/tabular/
COPY backend/libs/contracts/ ./libs/contracts/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/worker_agent/ ./services/worker_agent/
USER app
