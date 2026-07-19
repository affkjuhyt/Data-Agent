import os

PORT         = int(os.getenv("MODEL_SERVICE_PORT", "8001"))
EMBED_MODEL  = os.getenv("EMBED_MODEL",  "BAAI/bge-large-en-v1.5")
BGE_M3_MODEL = os.getenv("BGE_M3_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
REMOTE_EMBED_URL  = os.getenv("REMOTE_EMBED_URL",  "").rstrip("/")
REMOTE_RERANK_URL = os.getenv("REMOTE_RERANK_URL", "").rstrip("/")
REMOTE_API_KEY    = os.getenv("REMOTE_API_KEY", "")
REMOTE_TIMEOUT    = float(os.getenv("REMOTE_TIMEOUT", "300"))
REMOTE_LLM_URL    = os.getenv("REMOTE_LLM_URL", "").rstrip("/")
REMOTE_LLM_KEY    = os.getenv("REMOTE_LLM_KEY", "")
REMOTE_LLM_MODEL  = os.getenv("REMOTE_LLM_MODEL", "")

# ── Embedding cache + dynamic batching (KB-SCALE-10K.md lever #1) ──────────────
def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

EMBED_CACHE_ENABLED = _flag("EMBED_CACHE_ENABLED")
EMBED_CACHE_SIZE    = int(os.getenv("EMBED_CACHE_SIZE", "50000"))   # ~50k query vectors
EMBED_BATCH_ENABLED = _flag("EMBED_BATCH_ENABLED")
EMBED_BATCH_MAX     = int(os.getenv("EMBED_BATCH_MAX", "64"))       # texts per encode
EMBED_BATCH_WAIT_MS = float(os.getenv("EMBED_BATCH_WAIT_MS", "8"))  # coalesce window

POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://aeroflow:aeroflow_secret@postgres/aeroflow_kb",
)
