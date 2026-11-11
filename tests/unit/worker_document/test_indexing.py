"""A document's chunk vectors written to Qdrant (libs/vectors), against qdrant-client's in-process
Qdrant and a stand-in embeddings endpoint."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from qdrant_client import AsyncQdrantClient

from libs import vectors
from services.worker_document.indexing import Point, VectorIndex

pytestmark = [
    pytest.mark.unit,
    # The in-process Qdrant keeps no payload index and says so; the server does.
    pytest.mark.filterwarnings("ignore:Payload indexes have no effect:UserWarning"),
]

ORG, COLLECTION, DOCUMENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _index(qdrant: AsyncQdrantClient, batches: list[int]) -> VectorIndex:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batches.append(len(body["input"]))
        # A vector per text, distinct enough for cosine: the text's length and two constants.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [float(len(t)), 1.0, float(i + 1), 0.5]}
                    for i, t in enumerate(body["input"])
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return VectorIndex(client, qdrant=qdrant, embed_url="http://embed", embed_key="k", dimension=4)


async def _sync(
    index: VectorIndex, points: list[Point], deletes: list[uuid.UUID] | None = None
) -> None:
    await index.sync(
        "doc_x",
        org_id=ORG,
        collection_id=COLLECTION,
        document_id=DOCUMENT,
        embed_model="bge-m3",
        upserts=points,
        deletes=deletes or [],
    )


async def _stored(qdrant: AsyncQdrantClient) -> dict[str, dict]:
    points, _ = await qdrant.scroll("doc_x", limit=1000, with_payload=True)
    return {str(p.id): dict(p.payload or {}) for p in points}


@pytest.mark.req("AC-05-02-06")
async def test_every_point_records_its_embedding_model() -> None:
    qdrant = AsyncQdrantClient(location=":memory:")
    points = [Point(uuid.uuid4(), "Điều 5"), Point(uuid.uuid4(), "Điều 6")]
    await _sync(_index(qdrant, []), points)
    stored = await _stored(qdrant)
    assert set(stored) == {str(p.chunk_id) for p in points}
    assert {p["embed_model"] for p in stored.values()} == {"bge-m3"}
    assert {p["document_id"] for p in stored.values()} == {str(DOCUMENT)}
    # What the searches read back: a hit is a chunk id, of the collection's current model.
    hits = await vectors.nearest(
        qdrant, [vectors.Target("doc_x", "bge-m3")], [6.0, 1.0, 1.0, 0.5], 5
    )
    assert {chunk for chunk, _ in hits} == {p.chunk_id for p in points}


@pytest.mark.req("AC-05-02-05")
async def test_only_the_points_of_removed_chunks_are_deleted() -> None:
    qdrant = AsyncQdrantClient(location=":memory:")
    kept, gone = Point(uuid.uuid4(), "a"), Point(uuid.uuid4(), "b")
    await _sync(_index(qdrant, []), [kept, gone])
    added = Point(uuid.uuid4(), "c")
    await _sync(_index(qdrant, []), [added], [gone.chunk_id])
    assert set(await _stored(qdrant)) == {str(kept.chunk_id), str(added.chunk_id)}


async def test_a_missing_collection_is_created_with_the_model_dimension() -> None:
    qdrant = AsyncQdrantClient(location=":memory:")
    await _sync(_index(qdrant, []), [])
    info = await qdrant.get_collection("doc_x")
    assert info.config.params.vectors.size == 4  # type: ignore[union-attr]


async def test_chunks_are_embedded_in_batches() -> None:
    qdrant, batches = AsyncQdrantClient(location=":memory:"), []
    await _sync(_index(qdrant, batches), [Point(uuid.uuid4(), str(i)) for i in range(70)])
    assert batches == [32, 32, 6]
    assert len(await _stored(qdrant)) == 70


def test_unconfigured_index_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert VectorIndex.from_env(httpx.AsyncClient()) is None
