import os


MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio_secret")

BUCKET_ARTIFACTS = "aeroflow-artifacts"
BUCKET_AUDIT = "aeroflow-audit-archive"
