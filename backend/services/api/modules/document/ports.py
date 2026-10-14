"""What the document routes need from outside Postgres, behind small interfaces.

The document object store uses the deployment's S3-compatible MinIO endpoint. The secret and
vector ports remain explicit dependencies: their defaults refuse or contribute nothing, and tests
replace them with fakes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import BinaryIO, Protocol
from urllib.parse import urlparse

from services.api.kernel.problems import Problem

# D4: originals in da-raw-docs (immutable, invariant 1), page images in da-doc-derived.
RAW_BUCKET = "da-raw-docs"
DERIVED_BUCKET = "da-doc-derived"


class ObjectStore(Protocol):
    async def exists(self, bucket: str, key: str) -> bool: ...

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None: ...

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str: ...

    async def download(self, bucket: str, key: str) -> bytes: ...


class SecretStore(Protocol):
    async def write(self, org_id: uuid.UUID, name: str, values: dict[str, str]) -> str:
        """Stores the values and returns the reference kept in secret_ref."""
        ...


@dataclass(frozen=True)
class VectorTarget:
    qdrant_collection: str
    embed_model: str


class VectorSearch(Protocol):
    async def search(
        self, targets: list[VectorTarget], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        """(qdrant_point_id, score) of the nearest chunks, best first."""
        ...


def _unavailable(what: str) -> Problem:
    return Problem(503, "source_unavailable", f"{what} is not configured.")


class UnconfiguredStore:
    async def exists(self, bucket: str, key: str) -> bool:
        raise _unavailable("File storage")

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        raise _unavailable("File storage")

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str:
        raise _unavailable("File storage")

    async def download(self, bucket: str, key: str) -> bytes:
        raise _unavailable("File storage")


class MinioObjectStore:
    """S3-compatible store used for immutable originals and derived previews."""

    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self._endpoint = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            self._endpoint += f":{parsed.port}"
        self._key = parsed.username
        self._secret = parsed.password

    def _client(self):
        import aioboto3
        from botocore.config import Config

        return aioboto3.Session().client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._key,
            aws_secret_access_key=self._secret,
            config=Config(signature_version="s3v4"),
        )

    async def exists(self, bucket: str, key: str) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                await client.head_object(Bucket=bucket, Key=key)
                return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc
        except BotoCoreError as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                await client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=data,
                    ContentLength=size,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                return await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=ttl_seconds,
                )
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc

    async def download(self, bucket: str, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=bucket, Key=key)
                return await response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc


class UnconfiguredSecrets:
    async def write(self, org_id: uuid.UUID, name: str, values: dict[str, str]) -> str:
        raise _unavailable("The secret store")


class NoVectors:
    async def search(
        self, targets: list[VectorTarget], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        return []


_STORE, _SECRETS, _VECTORS = UnconfiguredStore(), UnconfiguredSecrets(), NoVectors()


def object_store() -> ObjectStore:
    import os

    url = os.getenv("MINIO_URL", "").strip()
    return MinioObjectStore(url) if url else _STORE


def secret_store() -> SecretStore:
    return _SECRETS


def vector_search() -> VectorSearch:
    return _VECTORS
