# syntax=docker/dockerfile:1
# AeroFlow KB Backend — FastAPI service
#
# Build context: repo root (one level above deploy/docker/)
#   cd <repo-root>
#   docker build -f backend/services/kb_backend/Dockerfile -t kb-backend .
#
# Or via Compose:
#   docker compose -f deploy/docker/docker-compose.kb-e2e.yml up kb-backend


# ── Stage 1: compile Python wheels ───────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build

# Build-time system deps:
#   gcc + libpq-dev  → asyncpg (C extension, links against libpq)
#   libssl-dev + libffi-dev → cryptography (required by snowflake-connector-python)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/services/kb_backend/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Runtime-only system dep: libpq5 is the shared library asyncpg links against.
# cryptography / snowflake-connector uses the OpenSSL already bundled in the
# python:3.13-slim base image, so no extra libssl package is needed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled site-packages from builder
COPY --from=builder /install /usr/local

# Copy source — ordered least-to-most frequently changed for layer cache efficiency
COPY backend/libs/ ./libs/
COPY backend/services/  ./services/

# Make all packages importable without an editable install
# PYTHONUNBUFFERED=1  → stdout/stderr flushed immediately (essential for Docker log collection)
# PYTHONDONTWRITEBYTECODE=1 → no .pyc files written inside the container
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Default service URLs (override via env or docker-compose) ─────────────────
# These match the names used in docker-compose.kb-e2e.yml
ENV POSTGRES_URL="postgresql+asyncpg://aeroflow:aeroflow_secret@postgres/aeroflow_kb" \
    MONGO_URL="mongodb://mongo:27017/dataagent" \
    MINIO_URL="http://minio:miniominio@minio:9000" \
    MINIO_BUCKET="knowledge-base" \
    KAFKA_URL="kafka:9092" \
    QDRANT_URL="http://qdrant:6333" \
    NEO4J_URL="bolt://neo4j:7687" \
    MODEL_SERVICE_URL="http://model_service:8001" \
    LITELLM_BASE_URL="http://litellm:4000" \
    LLM_MODEL="llama3"

# Create writable log directory before dropping root
RUN useradd --no-create-home --uid 1001 kb \
    && mkdir -p /app/logs \
    && chown kb:kb /app/logs
USER kb

EXPOSE 8000

# Probe the /health endpoint added to backend/services/kb_backend/main.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Workers are env-tunable. One async worker plateaus at ~1900 rps / ~1 core
# (measured, docs/KB-SCALE-10K.md §6); set UVICORN_WORKERS = CPU cores to use the rest
# and add headroom for CPU-bound steps (RRF fusion, serialize) + cold-embed load.
# Shell form so ${UVICORN_WORKERS} expands at runtime.
ENV UVICORN_WORKERS=1
CMD uvicorn services.kb_backend.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS} --log-level info
