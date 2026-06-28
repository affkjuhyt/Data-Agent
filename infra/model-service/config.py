import os

PORT         = int(os.getenv("MODEL_SERVICE_PORT", "8001"))
EMBED_MODEL  = os.getenv("EMBED_MODEL",  "BAAI/bge-large-en-v1.5")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
REMOTE_EMBED_URL  = os.getenv("REMOTE_EMBED_URL",  "").rstrip("/")
REMOTE_RERANK_URL = os.getenv("REMOTE_RERANK_URL", "").rstrip("/")
REMOTE_API_KEY    = os.getenv("REMOTE_API_KEY", "")
REMOTE_TIMEOUT    = float(os.getenv("REMOTE_TIMEOUT", "300"))
REMOTE_LLM_URL    = os.getenv("REMOTE_LLM_URL", "").rstrip("/")
REMOTE_LLM_KEY    = os.getenv("REMOTE_LLM_KEY", "")
REMOTE_LLM_MODEL  = os.getenv("REMOTE_LLM_MODEL", "")
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://aeroflow:aeroflow_secret@postgres/aeroflow_kb",
)
