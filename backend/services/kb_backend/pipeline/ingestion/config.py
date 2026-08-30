import os

LITELLM_BASE_URL    = os.getenv("LITELLM_BASE_URL",    "http://litellm:4000")
MODEL_SERVICE_URL   = os.getenv("MODEL_SERVICE_URL",   "http://model_service:8001")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
VLM_MODEL = os.getenv("VLM_MODEL", "qwen2.5-vl")

MINIO_BUCKET = os.getenv("MINIO_BUCKET", "knowledge-base")
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "200")) * 1024 * 1024

KAFKA_TOPIC_SILVER = os.getenv("KAFKA_TOPIC_SILVER", "kb.promote.silver")
KAFKA_TOPIC_GOLD = os.getenv("KAFKA_TOPIC_GOLD", "kb.promote.gold")

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")
CONFLICT_SIMILARITY_THRESHOLD = float(os.getenv("CONFLICT_SIMILARITY_THRESHOLD", "0.92"))
VLM_FRAME_INTERVAL = int(os.getenv("VLM_FRAME_INTERVAL", "30"))
