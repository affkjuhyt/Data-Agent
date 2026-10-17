"""The Qdrant adapter behind POST /v1/search/documents (its fusion with full-text search is in
tests/integration/api/test_document.py, against a fake of this port)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from services.api.modules.document.ports import (
    NoVectors,
    QdrantVectors,
    VectorTarget,
    vector_search,
)

pytestmark = pytest.mark.unit

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _patch(monkeypatch: pytest.MonkeyPatch, handle: Any) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw)
    )


def _qdrant(searches: list[tuple[str, dict]]) -> Any:
    results = {
        "doc_1": [{"id": str(A), "score": 0.71}, {"id": str(B), "score": 0.40}],
        "doc_2": [{"id": str(C), "score": 0.90}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/v1/embeddings":
            assert body == {"model": "embedder", "input": ["lãi suất 12 tháng"]}
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        name = request.url.path.split("/")[2]
        searches.append((name, body))
        if name not in results:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"result": results[name]})

    return handle


def _port() -> QdrantVectors:
    return QdrantVectors("http://qdrant", "http://embed", "k", "embedder")


@pytest.mark.req("AC-05-02-06")
async def test_neighbours_across_collections_best_first_of_the_current_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    searches: list[tuple[str, dict]] = []
    _patch(monkeypatch, _qdrant(searches))
    hits = await _port().search(
        [
            VectorTarget("doc_1", "bge-m3"),
            VectorTarget("doc_2", "bge-m3"),
            VectorTarget("doc_3", "bge-m3"),
        ],
        "lãi suất 12 tháng",
        2,
    )
    assert hits == [(C, 0.90), (A, 0.71)]
    assert [name for name, _ in searches] == ["doc_1", "doc_2", "doc_3"]
    assert searches[0][1]["filter"] == {
        "must": [{"key": "embed_model", "match": {"value": "bge-m3"}}]
    }
    assert searches[0][1]["vector"] == [0.1, 0.2]


async def test_an_unreachable_index_costs_recall_not_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _patch(monkeypatch, down)
    assert await _port().search([VectorTarget("doc_1", "bge-m3")], "q", 5) == []


def test_the_port_is_qdrant_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert isinstance(vector_search(), NoVectors)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant")
    monkeypatch.setenv("DOCUMENT_EMBED_URL", "http://tei")
    assert isinstance(vector_search(), QdrantVectors)
