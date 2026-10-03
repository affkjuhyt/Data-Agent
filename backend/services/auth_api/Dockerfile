# syntax=docker/dockerfile:1
FROM python:3.12-alpine

WORKDIR /app
ENV PYTHONPATH=/app

RUN apk add --no-cache gcc musl-dev libpq-dev curl

COPY backend/services/auth_api/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Code imports itself as `services.auth_api.xxx` and shared code as
# `libs.shared.xxx` — preserve both package paths.
COPY backend/libs/ ./libs/
# Only this service's tree, not all of backend/services: no service imports another,
# and copying them all rebuilt (and restarted) this image for every other service's edit.
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/auth_api/ ./services/auth_api/

# Drop root (alpine syntax) — read-only app surface.
RUN addgroup -S app && adduser -S -G app app
USER app
