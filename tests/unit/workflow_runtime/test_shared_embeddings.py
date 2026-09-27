"""libs.shared.embeddings — the one LiteLLM ``/v1/embeddings`` client.

HTTP is faked with ``httpx.MockTransport`` so the request shape (URL, bearer
header, ``{"model", "input"}`` body) and the response handling are asserted
without a network.
"""
from __future__ import annotations

import json

import httpx
import pytest

from libs.shared import embeddings

pytestmark = pytest.mark.unit


def _transport(handler):
    return httpx.MockTransport(handler)


def _ok(vectors: list[list[float]], *, shuffle: bool = False) -> httpx.Response:
    rows = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    if shuffle:
        rows = rows[::-1]
    return httpx.Response(200, json={"data": rows})


def test_embed_posts_the_litellm_shape(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok([[0.1, 0.2], [0.3, 0.4]])

    monkeypatch.setattr(embeddings.httpx, "post", lambda url, **kw: httpx.Client(transport=_transport(handler)).post(url, **kw))

    vectors = embeddings.embed(["a", "b"], base_url="http://llm:4000/", api_key="sk-x", model="embedder")

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["url"] == "http://llm:4000/v1/embeddings"
    assert seen["auth"] == "Bearer sk-x"
    assert seen["body"] == {"model": "embedder", "input": ["a", "b"]}


def test_embed_one_returns_a_single_vector(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["input"] == ["only"]
        return _ok([[1.0, 2.0]])

    monkeypatch.setattr(embeddings.httpx, "post", lambda url, **kw: httpx.Client(transport=_transport(handler)).post(url, **kw))

    assert embeddings.embed_one("only", base_url="http://llm", api_key="k") == [1.0, 2.0]


def test_vectors_are_returned_in_input_order_even_if_the_response_is_not(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok([[0.0], [1.0], [2.0]], shuffle=True)

    monkeypatch.setattr(embeddings.httpx, "post", lambda url, **kw: httpx.Client(transport=_transport(handler)).post(url, **kw))

    assert embeddings.embed(["x", "y", "z"], base_url="http://llm", api_key="k") == [[0.0], [1.0], [2.0]]


def test_a_short_response_is_refused_rather_than_misaligned(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok([[0.0]])

    monkeypatch.setattr(embeddings.httpx, "post", lambda url, **kw: httpx.Client(transport=_transport(handler)).post(url, **kw))

    with pytest.raises(ValueError, match="vectors for 2 inputs"):
        embeddings.embed(["x", "y"], base_url="http://llm", api_key="k")


def test_http_errors_propagate(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    monkeypatch.setattr(embeddings.httpx, "post", lambda url, **kw: httpx.Client(transport=_transport(handler)).post(url, **kw))

    with pytest.raises(httpx.HTTPStatusError):
        embeddings.embed(["x"], base_url="http://llm", api_key="k")


def test_empty_input_makes_no_request(monkeypatch):
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **kw: pytest.fail("should not call"))
    assert embeddings.embed([], base_url="http://llm", api_key="k") == []


async def test_aembed_uses_the_given_async_client():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok([[0.5, 0.5]])

    async with httpx.AsyncClient(transport=_transport(handler)) as client:
        vectors = await embeddings.aembed(["q"], base_url="http://llm:4000", api_key="k", client=client)
        one = await embeddings.aembed_one("q", base_url="http://llm:4000", api_key="k", client=client)

    assert vectors == [[0.5, 0.5]]
    assert one == [0.5, 0.5]
    assert seen["url"] == "http://llm:4000/v1/embeddings"
    assert seen["body"] == {"model": "embedder", "input": ["q"]}
