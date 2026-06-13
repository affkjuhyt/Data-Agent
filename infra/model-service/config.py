import os

PORT         = int(os.getenv("MODEL_SERVICE_PORT", "8001"))
EMBED_MODEL  = os.getenv("EMBED_MODEL",  "BAAI/bge-large-en-v1.5")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# asyncpg-style DSN (no +asyncpg dialect prefix)
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://aeroflow:aeroflow_secret@postgres/aeroflow_kb",
)
