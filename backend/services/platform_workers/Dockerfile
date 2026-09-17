# Build context is the repo root (set in compose) so backend/libs is reachable:
# the governance vocabulary (libs/shared/freshness.py), the embeddings client
# and the observability bootstrap are shared with the other services.
FROM python:3.12-slim
WORKDIR /app
COPY backend/services/platform_workers/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# web-crawler-worker's web_crawl connector needs a real Chromium — same
# install step as kb_promote_silver's Dockerfile, which already depends on
# Playwright for the identical fetch.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
# /app mirrors backend/: `libs.*` and `services.platform_workers.*` resolve as
# root packages, matching how the code imports after the repo restructure (the
# old flat copy left no `services` package → "No module named 'services'" loop).
COPY backend/libs ./libs
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/platform_workers/ ./services/platform_workers/
# Drop root — read-only app surface. /data is the audit bridge's STATE_FILE
# volume; pre-creating it owned by `app` means an empty named volume inherits
# writable ownership on first mount.
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data && chown app:app /data
USER app

