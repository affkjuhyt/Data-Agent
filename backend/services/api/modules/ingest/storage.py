"""Object storage for uploaded files (MinIO bucket da-ingest, UC-04-08 step 1)."""

from __future__ import annotations

from typing import BinaryIO, Protocol
from urllib.parse import urlparse

from services.api.kernel.problems import Problem

BUCKET = "da-ingest"


class ObjectStore(Protocol):
    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None: ...

    async def get(self, bucket: str, key: str) -> bytes: ...

    async def delete(self, bucket: str, key: str) -> None: ...


class UnconfiguredStore:
    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        raise Problem(503, "source_unavailable", "File storage is not configured.")

    async def get(self, bucket: str, key: str) -> bytes:
        raise Problem(503, "source_unavailable", "File storage is not configured.")

    async def delete(self, bucket: str, key: str) -> None:
        raise Problem(503, "source_unavailable", "File storage is not configured.")


_UNCONFIGURED = UnconfiguredStore()


class MinioObjectStore:
    """Write the immutable upload stream to the configured S3-compatible store."""

    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self._endpoint = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            self._endpoint += f":{parsed.port}"
        self._key = parsed.username
        self._secret = parsed.password

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        # Keep this import lazy so API tests can override object_store without requiring a live
        # MinIO endpoint. The upload stream is passed through; it is not duplicated in memory.
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
                await client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=data,
                    ContentLength=size,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc

    async def get(self, bucket: str, key: str) -> bytes:
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
                response = await client.get_object(Bucket=bucket, Key=key)
                async with response["Body"] as body:
                    return await body.read()
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc

    async def delete(self, bucket: str, key: str) -> None:
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
                await client.delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc


def _configured_store() -> ObjectStore:
    import os

    url = os.getenv("MINIO_URL", "").strip()
    return MinioObjectStore(url) if url else _UNCONFIGURED


def object_store() -> ObjectStore:
    """FastAPI dependency; tests can replace it while deployed API uses MinIO."""
    return _configured_store()
