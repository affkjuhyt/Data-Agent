# syntax=docker/dockerfile:1
# Build context is the repo root (the deploy's default): libs/contracts and
# libs/shared (auth, observability) are shared with the other services.
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY backend/services/indicator_api/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
# /app mirrors backend/: `libs.*` and `services.indicator_api.*` resolve as root packages.
COPY backend/libs ./libs
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/indicator_api/ ./services/indicator_api/
RUN addgroup --system app && adduser --system --ingroup app app
USER app
