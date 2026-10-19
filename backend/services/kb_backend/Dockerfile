# syntax=docker/dockerfile:1
# ── Stage 1: compile Python wheels ───────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build
# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/services/kb_backend/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --prefix=/install -r requirements.txt

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app
# DEBIAN_MIRROR replaces deb.debian.org where the link to it is slow (the inventory sets it;
# empty keeps upstream). apt verifies the archive's signatures, so a mirror cannot alter packages.
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then sed -i "s#^URIs: http://deb.debian.org/debian\$#URIs: $DEBIAN_MIRROR#" /etc/apt/sources.list.d/debian.sources; fi
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local

COPY backend/libs/ ./libs/
# Only this service's tree, not all of backend/services: no service imports another,
# and copying them all rebuilt (and restarted) this image for every other service's edit.
COPY backend/services/__init__.py ./services/__init__.py
COPY backend/services/kb_backend/ ./services/kb_backend/
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create writable log directory before dropping root
RUN useradd --no-create-home --uid 1001 kb \
    && mkdir -p /app/logs \
    && chown kb:kb /app/logs
USER kb
