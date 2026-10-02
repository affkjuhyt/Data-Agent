"""MinIO (immutable raw artifacts, read for evidence)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from minio import Minio

# Prefixed, not the shared MINIO_* names every pod gets from dataagent-config:
# those point at the knowledge-base bucket with root credentials.
ENDPOINT_ENV = "INDICATOR_API_MINIO_ENDPOINT"
BUCKET_ENV = "INDICATOR_API_MINIO_BUCKET"
ACCESS_KEY_ENV = "INDICATOR_API_MINIO_ACCESS_KEY"
SECRET_KEY_ENV = "INDICATOR_API_MINIO_SECRET_KEY"


class MinioAdapter:
    name = "minio"

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._endpoint = endpoint
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key

    @classmethod
    def from_env(cls) -> MinioAdapter | None:
        endpoint = os.environ.get(ENDPOINT_ENV, "").strip()
        if not endpoint:
            return None
        return cls(
            endpoint,
            os.environ.get(BUCKET_ENV, "").strip(),
            os.environ.get(ACCESS_KEY_ENV, ""),
            os.environ.get(SECRET_KEY_ENV, ""),
        )

    def ping(self) -> None:
        # A bucket check, not list_buckets(): a per-service principal is granted
        # its bucket, not ListAllMyBuckets.
        if not self._bucket:
            raise ValueError(f"{BUCKET_ENV} is not set")
        url = urlparse(self._endpoint)
        client = Minio(
            url.netloc or url.path,
            access_key=self._access_key,
            secret_key=self._secret_key,
            secure=url.scheme == "https",
        )
        if not client.bucket_exists(self._bucket):
            raise LookupError(f"bucket {self._bucket!r} does not exist")
