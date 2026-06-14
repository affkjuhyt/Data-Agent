import os

PORT         = int(os.getenv("MODEL_SERVICE_PORT", "8001"))
EMBED_MODEL  = os.getenv("EMBED_MODEL",  "BAAI/bge-large-en-v1.5")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# Remote compute proxy (e.g. a Colab GPU server behind cloudflared). When
# REMOTE_EMBED_URL is set, /embed FORWARDS there instead of computing locally —
# a strong GPU model serves embeddings while this box only proxies (keeps the
# tiny local model for health). REMOTE_RERANK_URL does the same for /rerank.
REMOTE_EMBED_URL  = os.getenv("REMOTE_EMBED_URL",  "").rstrip("/")
REMOTE_RERANK_URL = os.getenv("REMOTE_RERANK_URL", "").rstrip("/")
REMOTE_API_KEY    = os.getenv("REMOTE_API_KEY", "")
REMOTE_TIMEOUT    = float(os.getenv("REMOTE_TIMEOUT", "300"))

# LLM gateway: /generate forwards to an OpenAI-compatible server (e.g. the Colab
# vLLM/Qwen tunnel) when REMOTE_LLM_URL is set. Callers (ingestion, lightrag) send
# generic {prompt/messages, json_schema?} and never know the backend or its URL.
REMOTE_LLM_URL    = os.getenv("REMOTE_LLM_URL", "").rstrip("/")
REMOTE_LLM_KEY    = os.getenv("REMOTE_LLM_KEY", "")
REMOTE_LLM_MODEL  = os.getenv("REMOTE_LLM_MODEL", "")   # empty → discovered from /v1/models

# asyncpg-style DSN (no +asyncpg dialect prefix)
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://aeroflow:aeroflow_secret@postgres/aeroflow_kb",
)
