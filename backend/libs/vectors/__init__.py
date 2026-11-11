"""The document chunks' vectors in Qdrant, through qdrant-client: what worker-document writes and
what worker-agent's and the api's document search read.

Three services spoke Qdrant's REST API by hand, the two readers with the same copied search on
/points/search, which Qdrant has deprecated for /points/query. This is the one place that knows
the layout the writer and the readers share:

- one Qdrant collection per document collection, cosine distance, a keyword index on
  document_id (what a re-processed document's stale points are found by);
- a point's id is its chunk's id, so a search hit is a chunk id;
- every point carries embed_model, and a search reads only the points of the collection's
  current model (BR-18: vectors of two embedding models are never compared).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse


def connect(url: str, *, timeout: float = 15.0) -> AsyncQdrantClient:
    # REST, as the cluster serves it; no version handshake at start, so a worker comes up while
    # Qdrant is still starting and a search simply finds nothing until it answers.
    return AsyncQdrantClient(
        url=url, timeout=int(timeout), prefer_grpc=False, check_compatibility=False
    )


@dataclass(frozen=True)
class Target:
    """A Qdrant collection to search and the embedding model its current points were made with."""

    collection: str
    embed_model: str


@dataclass(frozen=True)
class ChunkPoint:
    chunk_id: uuid.UUID
    vector: Sequence[float]
    payload: dict[str, Any]


def _missing(exc: UnexpectedResponse) -> bool:
    return exc.status_code == 404


async def nearest(
    client: AsyncQdrantClient, targets: Iterable[Target], vector: Sequence[float], limit: int
) -> list[tuple[uuid.UUID, float]]:
    """(chunk id, cosine) best first, at most `limit`, over the targets' points of their model.
    A collection Qdrant does not have yet (nothing indexed there) adds nothing."""
    hits: list[tuple[uuid.UUID, float]] = []
    for target in targets:
        try:
            found = await client.query_points(
                collection_name=target.collection,
                query=list(vector),
                limit=limit,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="embed_model", match=models.MatchValue(value=target.embed_model)
                        )
                    ]
                ),
                with_payload=False,
            )
        except UnexpectedResponse as exc:
            if _missing(exc):
                continue
            raise
        except ValueError as exc:
            # The in-process client says a missing collection this way.
            if "not found" in str(exc).lower():
                continue
            raise
        hits.extend((uuid.UUID(str(point.id)), float(point.score)) for point in found.points)
    hits.sort(key=lambda hit: -hit[1])
    return hits[:limit]


async def ensure_collection(client: AsyncQdrantClient, name: str, dimension: int) -> bool:
    """Create the collection (and its document_id index) when it is missing; True if it was."""
    if await client.collection_exists(name):
        return False
    await client.create_collection(
        name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE)
    )
    await client.create_payload_index(
        name, field_name="document_id", field_schema=models.PayloadSchemaType.KEYWORD, wait=True
    )
    return True


async def delete_chunks(
    client: AsyncQdrantClient, name: str, chunk_ids: Iterable[uuid.UUID]
) -> None:
    ids: list[models.ExtendedPointId] = [str(i) for i in chunk_ids]
    if ids:
        await client.delete(name, points_selector=models.PointIdsList(points=ids), wait=True)


async def upsert_chunks(client: AsyncQdrantClient, name: str, points: Iterable[ChunkPoint]) -> None:
    batch = [
        models.PointStruct(id=str(p.chunk_id), vector=list(p.vector), payload=p.payload)
        for p in points
    ]
    if batch:
        await client.upsert(name, points=batch, wait=True)
