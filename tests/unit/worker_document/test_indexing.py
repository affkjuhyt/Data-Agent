from __future__ import annotations

import json
import uuid

import httpx
import pytest

from services.worker_document.indexing import Point, VectorIndex

pytestmark = pytest.mark.unit

ORG, COLLECTION, DOCUMENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _index(calls: list[tuple[str, str, dict]], *, exists: bool) -> VectorIndex:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/v1/embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [float(i)] * 4} for i in range(len(body["input"]))
                    ]
                },
            )
        if request.method == "GET":
            return httpx.Response(200 if exists else 404, json={})
        return httpx.Response(200, json={"result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return VectorIndex(
        client, qdrant_url="http://qdrant", embed_url="http://embed", embed_key="k", dimension=4
    )


async def _replace(
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


@pytest.mark.req("AC-05-02-06")
async def test_every_point_records_its_embedding_model() -> None:
    calls: list[tuple[str, str, dict]] = []
    points = [Point(uuid.uuid4(), "Điều 5"), Point(uuid.uuid4(), "Điều 6")]
    await _replace(_index(calls, exists=True), points)

    upserts = [body for method, path, body in calls if method == "PUT" and path.endswith("/points")]
    assert len(upserts) == 1
    written = upserts[0]["points"]
    assert [p["id"] for p in written] == [str(p.chunk_id) for p in points]
    assert {p["payload"]["embed_model"] for p in written} == {"bge-m3"}
    assert {p["payload"]["document_id"] for p in written} == {str(DOCUMENT)}
    assert written[1]["vector"] == [1.0] * 4


@pytest.mark.req("AC-05-02-05")
async def test_only_the_points_of_removed_chunks_are_deleted() -> None:
    calls: list[tuple[str, str, dict]] = []
    gone = uuid.uuid4()
    await _replace(_index(calls, exists=True), [Point(uuid.uuid4(), "a")], [gone])
    paths = [path for _, path, _ in calls]
    delete = paths.index("/collections/doc_x/points/delete")
    assert delete < paths.index("/collections/doc_x/points")
    assert calls[delete][2] == {"points": [str(gone)]}


async def test_nothing_removed_deletes_nothing() -> None:
    calls: list[tuple[str, str, dict]] = []
    await _replace(_index(calls, exists=True), [Point(uuid.uuid4(), "a")])
    assert "/collections/doc_x/points/delete" not in [path for _, path, _ in calls]


async def test_a_missing_collection_is_created_with_the_model_dimension() -> None:
    calls: list[tuple[str, str, dict]] = []
    await _replace(_index(calls, exists=False), [])
    created = [
        body for method, path, body in calls if method == "PUT" and path == "/collections/doc_x"
    ]
    assert created == [{"vectors": {"size": 4, "distance": "Cosine"}}]


async def test_chunks_are_embedded_in_batches() -> None:
    calls: list[tuple[str, str, dict]] = []
    await _replace(_index(calls, exists=True), [Point(uuid.uuid4(), str(i)) for i in range(70)])
    sizes = [len(body["input"]) for _, path, body in calls if path.endswith("/v1/embeddings")]
    assert sizes == [32, 32, 6]


def test_unconfigured_index_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert VectorIndex.from_env(httpx.AsyncClient()) is None
