# syntax=docker/dockerfile:1
# Build context = repo root: backends/sync.py maps KBModel / KBModelVersion through
# the shared ORM in libs.basemodel, so the image needs that package next to the app.
FROM python:3.11-slim

WORKDIR /app

# Pure API gateway since 04-09-2026: all inference runs on the GPU server
# (TEI embed/rerank + vLLM chat via REMOTE_*_URL). No torch, no local weights —
# the image went from ~3GB (CPU torch + sentence-transformers + downloaded
# models) to a few tens of MB, and startup no longer needs the network for HF.
COPY backend/services/model_service/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Only the ORM models, not the whole of libs: the rest pulls in Kafka, Mongo,
# Qdrant clients this gateway never uses.
COPY backend/libs/__init__.py ./libs/__init__.py
COPY backend/libs/basemodel/ ./libs/basemodel/
COPY backend/services/model_service/ ./

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app
