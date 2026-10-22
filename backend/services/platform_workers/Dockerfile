# syntax=docker/dockerfile:1
# Build context is the repo root (the deploy's default) so backend/libs is reachable:
# the governance vocabulary (libs/shared/freshness.py), the embeddings client
# and the observability bootstrap are shared with the other services.
FROM python:3.12-slim-bookworm
WORKDIR /app
# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
COPY backend/services/platform_workers/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
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

