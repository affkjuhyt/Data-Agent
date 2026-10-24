"""What the document routes need from outside Postgres, behind small interfaces.

The document object store uses the deployment's S3-compatible MinIO endpoint and the vector port
the deployment's Qdrant, with the query embedded through LiteLLM. The secret port is still an
explicit dependency whose default refuses; tests replace every port with a fake.
"""

from __future__ import annotations

import logging
import os
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
        except ClientError as exc:
            # A missing original is the document's fault, not the store's: 404, not 503.
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise Problem(404, "not_found", "The document's original file is missing.") from exc
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc
        except BotoCoreError as exc:
            raise Problem(503, "source_unavailable", "File storage is unavailable.") from exc


class UnconfiguredSecrets:
    async def write(self, org_id: uuid.UUID, name: str, values: dict[str, str]) -> str:
        raise _unavailable("The secret store")


class NoVectors:
    async def search(
        self, targets: list[VectorTarget], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        return []


class QdrantVectors:
    """Nearest chunks over the collections' Qdrant indexes (worker_document writes them).

    A failure here costs recall, not the answer: search still has its full-text half, so the
    port logs and contributes nothing rather than failing the request.
    """

    def __init__(self, qdrant_url: str, embed_url: str, embed_key: str, embed_route: str) -> None:
        self._qdrant = qdrant_url.rstrip("/")
        self._embed_url = embed_url.rstrip("/")
        self._embed_key = embed_key
        self._route = embed_route

    async def search(
        self, targets: list[VectorTarget], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        import httpx

        if not targets:
            return []
        hits: list[tuple[uuid.UUID, float]] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._embed_url}/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._embed_key}"},
                    json={"model": self._route, "input": [query]},
                )
                response.raise_for_status()
                vector = response.json()["data"][0]["embedding"]
                for target in targets:
                    found = await client.post(
                        f"{self._qdrant}/collections/{target.qdrant_collection}/points/search",
                        json={
                            "vector": vector,
                            "limit": limit,
                            # BR-18: only points of the collection's current embedding model.
                            "filter": {
                                "must": [
                                    {"key": "embed_model", "match": {"value": target.embed_model}}
                                ]
                            },
                        },
                    )
                    if found.status_code == 404:
                        continue  # nothing indexed in this collection yet
                    found.raise_for_status()
                    hits.extend(
                        (uuid.UUID(str(p["id"])), float(p["score"])) for p in found.json()["result"]
                    )
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            _log.warning("vector search unavailable; keyword results only", exc_info=True)
            return []
        hits.sort(key=lambda hit: -hit[1])
        return hits[:limit]


_log = logging.getLogger(__name__)
_STORE, _SECRETS, _VECTORS = UnconfiguredStore(), UnconfiguredSecrets(), NoVectors()


def object_store() -> ObjectStore:
    url = os.getenv("MINIO_URL", "").strip()
    return MinioObjectStore(url) if url else _STORE


def secret_store() -> SecretStore:
    return _SECRETS


def vector_search() -> VectorSearch:
    qdrant = os.getenv("QDRANT_URL", "").strip()
    # Same endpoint worker_document embeds with (DOCUMENT_EMBED_URL, else the LiteLLM gateway).
    embed = (os.getenv("DOCUMENT_EMBED_URL") or os.getenv("LITELLM_BASE_URL") or "").strip()
    if not qdrant or not embed:
        return _VECTORS
    return QdrantVectors(
        qdrant,
        embed,
        os.getenv("LITELLM_API_KEY", ""),
        # The LiteLLM route serving collection.embed_model; the same one worker_document embeds with.
        os.getenv("DOCUMENT_EMBED_ROUTE", "embedder"),
    )
