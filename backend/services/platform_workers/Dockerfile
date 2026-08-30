# Build context is the repo root (set in compose) so backend/libs is reachable:
# the governance vocabulary (libs/shared/freshness.py), the embeddings client
# and the observability bootstrap are shared with the other services.
FROM python:3.12-slim
WORKDIR /app
COPY backend/services/platform_workers/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# libs/ keeps its real path so `import libs.shared.x` works the same here as
# from the repo root in tests.
COPY backend/libs ./libs
COPY backend/services/platform_workers/ ./
# Drop root — read-only app surface. /data is the audit bridge's STATE_FILE
# volume; pre-creating it owned by `app` means an empty named volume inherits
# writable ownership on first mount.
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data && chown app:app /data
USER app
# WORKER=memory|schema-crawler|audit picks the loop via the worker package.
CMD ["python", "-u", "-m", "services.platform_workers"]
