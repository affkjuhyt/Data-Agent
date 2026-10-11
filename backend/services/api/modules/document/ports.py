"""What the document routes need from outside Postgres, behind small interfaces.

The api has no MinIO, OpenBao or Qdrant client yet. Each port is a FastAPI dependency whose
default refuses (503 source_unavailable) or, for vector search, contributes nothing, so search
answers from the Postgres full-text index alone. A deployment wires real clients in with
`app.dependency_overrides[...]`; the tests do the same with fakes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import BinaryIO, Protocol

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
    return _STORE


def secret_store() -> SecretStore:
    return _SECRETS


def vector_search() -> VectorSearch:
    return _VECTORS
