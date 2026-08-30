FROM python:3.11-slim

WORKDIR /app

# Install CPU-only torch first so sentence-transformers doesn't pull the ~5GB CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        sentence-transformers \
        pydantic \
        asyncpg

# Model weights are NOT baked into the image. They download at runtime into HF_HOME,
# which the compose mounts to a host volume (deploy/docker/model-service/models) so the weights
# persist across container recreates. Trade-offs:
#   + image is ~1.5GB smaller, and never carries model variants a stack doesn't use
#   - the FIRST start needs network and is slower (downloads the configured EMBED_MODEL
#     + RERANK_MODEL); every start after that reuses the cached weights.
ENV HF_HOME=/app/models \
    SENTENCE_TRANSFORMERS_HOME=/app/models
RUN mkdir -p /app/models

COPY . .

# Drop root — weights download at runtime into /app/models, so it must be
# writable by the app user (the compose host-volume mount inherits this owner).
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app/models
USER app

EXPOSE 8001

CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
