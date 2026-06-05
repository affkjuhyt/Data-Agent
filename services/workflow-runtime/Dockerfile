# TODO(production): pin by digest để tránh supply-chain attack.
# Lấy digest bằng: docker pull python:3.11.9-slim && docker inspect python:3.11.9-slim --format='{{index .RepoDigests 0}}'
# Sau đó đổi dòng dưới thành: FROM python:3.11.9-slim@sha256:<digest>
FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/workflow-runtime/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user
RUN addgroup --system app && adduser --system --ingroup app app && \
    mkdir -p /app/logs && chown app:app /app/logs

COPY --chown=app:app basemodel/ ./basemodel/
COPY --chown=app:app services/database_connector/ ./services/database_connector/
COPY --chown=app:app services/log_set_up/ ./services/log_set_up/
COPY --chown=app:app app/ ./app/
COPY --chown=app:app services/workflow-runtime/ .

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

# Production: gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
