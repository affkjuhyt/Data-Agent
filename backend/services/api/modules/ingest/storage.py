"""Object storage for uploaded files (MinIO bucket da-ingest, UC-04-08 step 1).

The api has no MinIO client yet, so the routes depend on this small interface and the app
answers 503 source_unavailable until one is wired in (`app.dependency_overrides[object_store]`,
tests do the same with a fake). The original is written once and never rewritten: raw is
immutable (invariant 1 of docs/v2/00-architecture-v2.md); every key carries the upload's own id.
"""

from __future__ import annotations

from typing import BinaryIO, Protocol

from services.api.kernel.problems import Problem

BUCKET = "da-ingest"


class ObjectStore(Protocol):
    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None: ...


class UnconfiguredStore:
    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        raise Problem(503, "source_unavailable", "File storage is not configured.")


_UNCONFIGURED = UnconfiguredStore()


def object_store() -> ObjectStore:
    """FastAPI dependency; replaced where a real store is configured."""
    return _UNCONFIGURED
