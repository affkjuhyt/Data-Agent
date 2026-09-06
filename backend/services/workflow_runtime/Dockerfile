# TODO(production): pin by digest để tránh supply-chain attack.
# Lấy digest bằng: docker pull python:3.11.9-slim && docker inspect python:3.11.9-slim --format='{{index .RepoDigests 0}}'
# Sau đó đổi dòng dưới thành: FROM python:3.11.9-slim@sha256:<digest>
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/services/workflow_runtime/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user
RUN addgroup --system app && adduser --system --ingroup app app
RUN mkdir -p /app/logs && chown -R app:app /app/logs

COPY --chown=app:app backend/libs/ ./libs/
COPY --chown=app:app backend/services/workflow_runtime/ .

USER app
