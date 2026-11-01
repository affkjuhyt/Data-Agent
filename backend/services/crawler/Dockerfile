# syntax=docker/dockerfile:1
# The `crawler` block of ADR-28 (09 §1). Build context is the repo root.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
# Before any COPY: a code change then reuses this layer instead of rerunning it.
RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/services/crawler/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/dataagent_db/ ./libs/dataagent_db/
COPY backend/libs/tabular/ ./libs/tabular/
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/crawler/ ./services/crawler/
USER app
