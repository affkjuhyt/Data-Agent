"""libs/vectors against qdrant-client's in-process Qdrant: the layout the writer and the two
readers share, and BR-18 (vectors of two embedding models are never compared)."""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import AsyncQdrantClient

from libs import vectors
from libs.vectors import ChunkPoint, Target

pytestmark = [
    pytest.mark.unit,
    # The in-process Qdrant keeps no payload index and says so; the server does (libs/vectors).
    pytest.mark.filterwarnings("ignore:Payload indexes have no effect:UserWarning"),
]

DOC = str(uuid.uuid4())


@pytest.fixture
async def qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(location=":memory:")


def _point(vector: list[float], model: str = "bge-m3") -> ChunkPoint:
    return ChunkPoint(uuid.uuid4(), vector, {"document_id": DOC, "embed_model": model})


async def test_a_collection_is_made_once(qdrant: AsyncQdrantClient) -> None:
    assert await vectors.ensure_collection(qdrant, "doc_a", 3) is True
    assert await vectors.ensure_collection(qdrant, "doc_a", 3) is False


async def test_the_nearest_chunks_come_back_best_first(qdrant: AsyncQdrantClient) -> None:
    await vectors.ensure_collection(qdrant, "doc_a", 3)
    near, far = _point([1.0, 0.0, 0.0]), _point([0.0, 1.0, 0.0])
    await vectors.upsert_chunks(qdrant, "doc_a", [near, far])
    hits = await vectors.nearest(qdrant, [Target("doc_a", "bge-m3")], [0.9, 0.1, 0.0], 5)
    assert [chunk for chunk, _ in hits] == [near.chunk_id, far.chunk_id]
    assert hits[0][1] > hits[1][1]


async def test_only_the_current_models_points_are_compared(qdrant: AsyncQdrantClient) -> None:
    await vectors.ensure_collection(qdrant, "doc_a", 3)
    current, old = _point([1.0, 0.0, 0.0]), _point([1.0, 0.0, 0.0], model="old-model")
    await vectors.upsert_chunks(qdrant, "doc_a", [current, old])
    hits = await vectors.nearest(qdrant, [Target("doc_a", "bge-m3")], [1.0, 0.0, 0.0], 5)
    assert [chunk for chunk, _ in hits] == [current.chunk_id]


async def test_a_collection_with_nothing_indexed_adds_nothing(qdrant: AsyncQdrantClient) -> None:
    await vectors.ensure_collection(qdrant, "doc_a", 3)
    point = _point([1.0, 0.0, 0.0])
    await vectors.upsert_chunks(qdrant, "doc_a", [point])
    targets = [Target("doc_missing", "bge-m3"), Target("doc_a", "bge-m3")]
    hits = await vectors.nearest(qdrant, targets, [1.0, 0.0, 0.0], 5)
    assert [chunk for chunk, _ in hits] == [point.chunk_id]


async def test_removed_chunks_lose_their_points(qdrant: AsyncQdrantClient) -> None:
    await vectors.ensure_collection(qdrant, "doc_a", 3)
    keep, drop = _point([1.0, 0.0, 0.0]), _point([0.0, 1.0, 0.0])
    await vectors.upsert_chunks(qdrant, "doc_a", [keep, drop])
    await vectors.delete_chunks(qdrant, "doc_a", [drop.chunk_id])
    hits = await vectors.nearest(qdrant, [Target("doc_a", "bge-m3")], [0.0, 1.0, 0.0], 5)
    assert [chunk for chunk, _ in hits] == [keep.chunk_id]


async def test_across_collections_the_limit_keeps_the_best(qdrant: AsyncQdrantClient) -> None:
    for name in ("doc_a", "doc_b"):
        await vectors.ensure_collection(qdrant, name, 3)
    a, b = _point([1.0, 0.0, 0.0]), _point([0.7, 0.7, 0.0])
    await vectors.upsert_chunks(qdrant, "doc_a", [a])
    await vectors.upsert_chunks(qdrant, "doc_b", [b])
    targets = [Target("doc_b", "bge-m3"), Target("doc_a", "bge-m3")]
    hits = await vectors.nearest(qdrant, targets, [1.0, 0.0, 0.0], 1)
    assert [chunk for chunk, _ in hits] == [a.chunk_id]
