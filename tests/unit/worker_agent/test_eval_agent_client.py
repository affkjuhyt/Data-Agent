"""The release eval client talks to the V3 question API."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from model.eval import agent_client

pytestmark = pytest.mark.unit


def test_v3_client_requires_an_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="EVAL_ACCESS_TOKEN"):
        asyncio.run(agent_client.ask_live_agent("http://kong", "unused", "space-1", "hi?"))


def test_v3_client_posts_questions_and_reads_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_ACCESS_TOKEN", "access-token")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/questions":
            return httpx.Response(202, json={"question_id": "q-1"})
        if request.url.path == "/v1/questions/q-1":
            return httpx.Response(200, json={"status": "answered", "answer_id": "a-1"})
        return httpx.Response(200, json={"text": "Doanh thu là 42."})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*_: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_async_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(agent_client.httpx, "AsyncClient", fake_async_client)
    answer = asyncio.run(agent_client.ask_live_agent("http://kong", "unused", "space-1", "hi?"))

    assert answer == "Doanh thu là 42."
    assert seen[0].url.path == "/v1/questions"
    assert seen[0].headers["authorization"] == "Bearer access-token"
    assert seen[0].headers["x-data-space"] == "space-1"
    assert json.loads(seen[0].content) == {"text": "hi?", "stream": True}
