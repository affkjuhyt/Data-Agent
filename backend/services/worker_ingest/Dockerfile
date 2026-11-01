# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
# Before any COPY: a code change then reuses this layer instead of rerunning it.
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/worker_ingest/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/worker_ingest/ ./services/worker_ingest/
USER app
