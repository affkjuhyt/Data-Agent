FROM python:3.11-slim

WORKDIR /app

# Pure API gateway since 04-09-2026: all inference runs on the GPU server
# (TEI embed/rerank + vLLM chat via REMOTE_*_URL). No torch, no local weights —
# the image went from ~3GB (CPU torch + sentence-transformers + downloaded
# models) to a few tens of MB, and startup no longer needs the network for HF.
RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        pydantic \
        asyncpg

COPY . .

# Drop root — read-only app surface.
RUN addgroup --system app && adduser --system --ingroup app app
USER app

EXPOSE 8001

CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
