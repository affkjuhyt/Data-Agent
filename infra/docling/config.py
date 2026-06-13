import os

PORT = int(os.getenv("DOCLING_PORT", "8002"))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
USE_VLM = os.getenv("USE_VLM", "false").lower() == "true"
