# TODO(production): pin by digest để tránh supply-chain attack.
# Lấy digest bằng: docker pull python:3.11.9-slim && docker inspect python:3.11.9-slim --format='{{index .RepoDigests 0}}'
# Sau đó đổi dòng dưới thành: FROM python:3.11.9-slim@sha256:<digest>
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/services/workflow_runtime/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user
RUN addgroup --system app && adduser --system --ingroup app app
RUN mkdir -p /app/logs && chown -R app:app /app/logs

# Preserve the services/workflow_runtime/... package path — the code imports
# itself as `services.workflow_runtime.xxx` throughout (same convention as
# kb_backend's Dockerfile), so this can't be flattened into /app directly.
COPY --chown=app:app backend/libs/ ./libs/
COPY --chown=app:app backend/services/ ./services/

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

# Production: gunicorn -k uvicorn.workers.UvicornWorker -w 4 services.workflow_runtime.main:app
CMD ["uvicorn", "services.workflow_runtime.main:app", "--host", "0.0.0.0", "--port", "8001"]
