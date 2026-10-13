"""Writing a file worker-agent produced (an answer export) to MinIO.

MINIO_URL carries the endpoint and the credentials (http://key:secret@host:port), as the api's
read-only adapter takes it (services/api/modules/workspace/report_storage.py): the api reads and
signs, the workers write. Without MINIO_URL there is no store and the work that needs one fails
saying so.
"""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse


class StoreUnavailable(RuntimeError):
    """No object store is configured, or it refused the write."""


class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None: ...


class MinioStore:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self._endpoint = f"{parsed.scheme}://{parsed.hostname}" + (
            f":{parsed.port}" if parsed.port else ""
        )
        self._key, self._secret = parsed.username, parsed.password

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        import aioboto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._key,
                aws_secret_access_key=self._secret,
                config=Config(signature_version="s3v4"),
            ) as client:
                await client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise StoreUnavailable(
                f"the object store refused the file: {type(exc).__name__}"
            ) from exc


class NoStore:
    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        raise StoreUnavailable("MINIO_URL is not set")


_store: ObjectStore | None = None


def store() -> ObjectStore:
    global _store
    if _store is None:
        url = os.environ.get("MINIO_URL", "").strip()
        _store = MinioStore(url) if url else NoStore()
    return _store
