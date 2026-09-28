"""model_service app: route table, health, and the embed/rerank/generate paths
against a faked remote GPU server.

model_service has no authentication of its own (it is reached only in-cluster),
so what is pinned here is the wiring: env -> bootstrap -> registry -> caller ->
HTTP request shape, and the error mapping of each route.

The service imports by bare name (`import config as cfg`, `from backends
import ...`) because its image is built from the service dir. `config`,
`backends` and `app` are generic names, so each test imports them fresh from
the service dir and the previous sys.modules entries are restored afterwards.
`config` reads env at import time, which is the other reason for a fresh import
per test. The remote side is `urllib.request.urlopen`, replaced by a fake that
answers TEI /info, /embed, /rerank and OpenAI /v1/chat/completions.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
import types
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from .._loader import SERVICES

pytestmark = [
    pytest.mark.unit,
    # The image ships Starlette 1.x, whose TestClient wants `httpx2` and warns
    # when it falls back to the `httpx` that requirements-test.txt installs. The
    # suite runs with filterwarnings=error, and this warning is not a
    # DeprecationWarning subclass, so the global ignore does not cover it.
    pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning"),
]

MODEL_SERVICE_DIR = SERVICES / "model_service"
_BARE = ("app", "config", "backends")

EMBED_URL = "http://tei-embed.test"
RERANK_URL = "http://tei-rerank.test"
LLM_URL = "http://vllm.test"


class FakeRemote:
    """Stands in for the GPU server's HTTP endpoints; records every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat_reply = '{"answer": 42}'
        self.fail_chat = False

    def urlopen(self, req: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.requests.append({"method": req.get_method(), "url": req.full_url, "body": body,
                              "auth": req.get_header("Authorization")})
        url = req.full_url
        if url.endswith("/info"):
            name = "bge-m3" if url.startswith(EMBED_URL) else "bge-reranker"
            return _response({"model_id": name})
        if url == EMBED_URL + "/embed":
            return _response([[float(len(t)), 0.5] for t in body["inputs"]])
        if url == RERANK_URL + "/rerank":
            return _response([{"index": i, "score": s}
                              for i, s in enumerate([0.1, 0.9, 0.5][: len(body["texts"])])])
        if url == LLM_URL + "/v1/chat/completions":
            if self.fail_chat:
                raise OSError("connection reset by GPU server")
            return _response({"choices": [{"message": {"content": self.chat_reply}}]})
        raise AssertionError(f"unexpected remote call {req.get_method()} {url}")

    def calls_to(self, suffix: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["url"].endswith(suffix)]


def _response(payload: Any) -> io.BytesIO:
    # BytesIO is a context manager with .read(), which is all the callers use.
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> FakeRemote:
    fake = FakeRemote()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture
def load_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., types.ModuleType]]:
    def is_bare(name: str) -> bool:
        return any(name == m or name.startswith(m + ".") for m in _BARE)

    saved = {n: m for n, m in sys.modules.items() if is_bare(n)}

    def load(**env: str) -> types.ModuleType:
        for key in ("REMOTE_EMBED_URL", "REMOTE_RERANK_URL", "REMOTE_LLM_URL",
                    "REMOTE_API_KEY", "REMOTE_LLM_KEY", "REMOTE_LLM_MODEL", "POSTGRES_DSN"):
            monkeypatch.delenv(key, raising=False)
        # Without the batcher the embed path runs the encode in the default
        # executor; the batcher's queue would be bound to one event loop and
        # outlive the TestClient that started it.
        monkeypatch.setenv("EMBED_BATCH_ENABLED", "false")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for name in [n for n in sys.modules if is_bare(n)]:
            del sys.modules[name]
        module = importlib.import_module("app")

        async def no_db_sync(_registry: Any) -> None:
            return None

        # The KBModel id sync needs Postgres; unit scope stops at the registry.
        monkeypatch.setattr(module, "sync_with_retry", no_db_sync)
        return module

    monkeypatch.syspath_prepend(str(MODEL_SERVICE_DIR))
    try:
        yield load
    finally:
        for name in [n for n in sys.modules if is_bare(n)]:
            del sys.modules[name]
        sys.modules.update(saved)


REMOTE_ENV = {
    "REMOTE_EMBED_URL": EMBED_URL,
    "REMOTE_RERANK_URL": RERANK_URL,
    "REMOTE_LLM_URL": LLM_URL,
    "REMOTE_API_KEY": "tei-key",
    "REMOTE_LLM_KEY": "llm-key",
    "REMOTE_LLM_MODEL": "qwen3-8b",
}


@pytest.fixture
def client(load_app: Callable[..., types.ModuleType], remote: FakeRemote) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    app_module = load_app(**REMOTE_ENV)
    # `with` runs the lifespan, i.e. the real bootstrap against the fake remote.
    with TestClient(app_module.create_app(), raise_server_exceptions=False) as c:
        yield c


def test_route_table(load_app: Callable[..., types.ModuleType]) -> None:
    app = load_app().create_app()
    # Read from the OpenAPI schema: the image's FastAPI (0.14x) no longer
    # flattens an included router into `app.routes` as APIRoute objects.
    routes = {(method.upper(), path)
              for path, ops in app.openapi()["paths"].items() for method in ops}
    assert routes == {
        ("GET", "/health"),
        ("GET", "/metrics"),
        ("POST", "/embed"),
        ("POST", "/v1/embeddings"),
        ("POST", "/rerank"),
        ("POST", "/generate"),
    }


class TestHealth:
    def test_503_until_models_are_registered(self, load_app: Callable[..., types.ModuleType]) -> None:
        from fastapi.testclient import TestClient

        # No `with`: lifespan never runs, so the registry is empty.
        resp = TestClient(load_app().create_app()).get("/health")
        assert resp.status_code == 503
        assert resp.json() == {"status": "loading",
                               "llm": {"configured": False, "ready": False, "model": None}}

    def test_ok_after_bootstrap(self, client: Any, remote: FakeRemote) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["llm"] == {"configured": True, "ready": True, "model": "qwen3-8b"}
        # Bootstrap identified both TEI endpoints by probing /info with the key.
        assert [r["auth"] for r in remote.calls_to("/info")] == ["Bearer tei-key", "Bearer tei-key"]

    def test_non_tei_endpoint_is_not_registered(
        self, load_app: Callable[..., types.ModuleType], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        def refuse(req: urllib.request.Request, timeout: float | None = None) -> Any:
            raise OSError("404 on /info")

        monkeypatch.setattr(urllib.request, "urlopen", refuse)
        app_module = load_app(REMOTE_EMBED_URL=EMBED_URL, REMOTE_RERANK_URL=RERANK_URL)
        with TestClient(app_module.create_app()) as c:
            assert c.get("/health").status_code == 503
            assert c.post("/embed", json={"texts": ["x"]}).status_code == 503


class TestEmbed:
    def test_vectors_in_request_order_and_cached(self, client: Any, remote: FakeRemote) -> None:
        resp = client.post("/embed", json={"texts": ["gold", "brent crude"]})
        assert resp.status_code == 200
        assert resp.json()["embeddings"] == [[4.0, 0.5], [11.0, 0.5]]
        [call] = remote.calls_to("/embed")
        assert call["body"] == {"inputs": ["gold", "brent crude"]}
        assert call["auth"] == "Bearer tei-key"

        # Whitespace-folded repeat is a cache hit: no second remote call.
        again = client.post("/embed", json={"texts": ["gold  "]})
        assert again.json()["embeddings"] == [[4.0, 0.5]]
        assert len(remote.calls_to("/embed")) == 1
        assert "kb_embed_cache_hits 1" in client.get("/metrics").text

    def test_openai_shape(self, client: Any) -> None:
        resp = client.post("/v1/embeddings", json={"input": "oil"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == [{"object": "embedding", "index": 0, "embedding": [3.0, 0.5]}]
        assert body["usage"] == {"prompt_tokens": 1, "total_tokens": 1}

    def test_texts_must_be_a_list(self, client: Any, remote: FakeRemote) -> None:
        resp = client.post("/embed", json={"texts": "gold"})
        assert resp.status_code == 422
        assert remote.calls_to("/embed") == []

    def test_unknown_model_id_is_422(self, client: Any) -> None:
        resp = client.post("/embed", json={"texts": ["x"],
                                           "model_id": "00000000-0000-0000-0000-00000000dead"})
        assert resp.status_code == 422
        assert "not in registry" in resp.json()["detail"]


class TestRerank:
    def test_sorted_by_score_and_cut_to_top_n(self, client: Any, remote: FakeRemote) -> None:
        resp = client.post("/rerank", json={"query": "q", "documents": ["a", "b", "c"], "top_n": 2})
        assert resp.status_code == 200
        assert resp.json() == {"results": [{"index": 1, "relevance_score": 0.9},
                                           {"index": 2, "relevance_score": 0.5}]}
        [call] = remote.calls_to("/rerank")
        assert call["body"] == {"query": "q", "texts": ["a", "b", "c"]}

    def test_missing_documents_is_422(self, client: Any) -> None:
        assert client.post("/rerank", json={"query": "q"}).status_code == 422


class TestGenerate:
    def test_prompt_reaches_llm_as_json_mode_chat(self, client: Any, remote: FakeRemote) -> None:
        resp = client.post("/generate", json={"prompt": "price?", "system_prompt": "be brief",
                                              "max_tokens": 64})
        assert resp.status_code == 200
        assert resp.json() == {"content": '{"answer": 42}'}

        [call] = remote.calls_to("/v1/chat/completions")
        body = call["body"]
        assert call["auth"] == "Bearer llm-key"
        assert body["model"] == "qwen3-8b"
        assert body["messages"] == [{"role": "system", "content": "be brief"},
                                    {"role": "user", "content": "price?"}]
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 64
        # Qwen3 <think> blocks would break every JSON-parsing consumer.
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_unknown_model_is_422(self, client: Any, remote: FakeRemote) -> None:
        resp = client.post("/generate", json={"prompt": "x", "model": "gpt-9"})
        assert resp.status_code == 422
        assert remote.calls_to("/v1/chat/completions") == []

    def test_no_llm_configured_is_503(self, load_app: Callable[..., types.ModuleType],
                                      remote: FakeRemote) -> None:
        from fastapi.testclient import TestClient

        app_module = load_app(REMOTE_EMBED_URL=EMBED_URL)
        with TestClient(app_module.create_app()) as c:
            assert c.post("/generate", json={"prompt": "x"}).status_code == 503

    def test_remote_failure_is_500_and_counted(self, client: Any, remote: FakeRemote) -> None:
        remote.fail_chat = True
        resp = client.post("/generate", json={"prompt": "x"})
        assert resp.status_code == 500
        metrics = client.get("/metrics").text
        assert "kb_llm_generate_total 1" in metrics
        assert "kb_llm_generate_errors_total 1" in metrics
