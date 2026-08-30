"""Qdrant client + idempotent collection bootstrap."""
from __future__ import annotations

import logging

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from services.platform_workers.common.settings import QdrantSettings

log = logging.getLogger("platform-workers.qdrant")


def client(settings: QdrantSettings | None = None) -> QdrantClient:
    settings = settings or QdrantSettings()
    return QdrantClient(url=settings.url)


def ensure_collection(qdrant: QdrantClient, name: str, dim: int) -> None:
    """Create ``name`` (cosine, ``dim`` wide) unless it already exists."""
    names = [c.name for c in qdrant.get_collections().collections]
    if name not in names:
        qdrant.create_collection(name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
        log.info("qdrant collection '%s' created (dim=%d)", name, dim)
