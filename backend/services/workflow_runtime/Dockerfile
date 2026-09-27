# syntax=docker/dockerfile:1
# TODO(production): pin by digest để tránh supply-chain attack.
# Lấy digest bằng: docker pull python:3.11.9-slim && docker inspect python:3.11.9-slim --format='{{index .RepoDigests 0}}'
# Sau đó đổi dòng dưới thành: FROM python:3.11.9-slim@sha256:<digest>
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/services/workflow_runtime/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Non-root user
RUN addgroup --system app && adduser --system --ingroup app app
RUN mkdir -p /app/logs && chown -R app:app /app/logs

# Preserve the services/workflow_runtime/... package path — the code imports
# itself as `services.workflow_runtime.xxx` throughout (same convention as
# kb_backend's Dockerfile), so this can't be flattened into /app directly.
COPY --chown=app:app backend/libs/ ./libs/
COPY --chown=app:app backend/services/ ./services/

USER app
