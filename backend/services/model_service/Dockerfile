# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# Pure API gateway since 04-09-2026: all inference runs on the GPU server
# (TEI embed/rerank + vLLM chat via REMOTE_*_URL). No torch, no local weights —
# the image went from ~3GB (CPU torch + sentence-transformers + downloaded
# models) to a few tens of MB, and startup no longer needs the network for HF.
RUN --mount=type=cache,target=/root/.cache/pip pip install \
        fastapi \
        "uvicorn[standard]" \
        pydantic \
        asyncpg

COPY . .

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

