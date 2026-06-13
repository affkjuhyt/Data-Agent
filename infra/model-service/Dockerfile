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

# Pre-download all embedding model variants that are seeded in KBModel (init.sql).
# Adding a new seeded variant here keeps startup instant for that model too.
ARG RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-large-en-v1.5'); \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
CrossEncoder('${RERANK_MODEL}')"

COPY . .

EXPOSE 8001

CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
