"""model.eval.agent_client.ask_live_agent — workflow_runtime's
`/api/conversations/run` only trusts a caller Kong itself verified
(backend/libs/shared/auth.py's resolve_caller); calling it directly needs the
same X-Kong-Verified/X-Gateway-Key/X-Tenant-Id headers model/bench/e2e.py
already sends. Before this fix, ask_live_agent sent none of them and every
online-tier grounding case without its own `contexts` (the ones meant to
exercise real KB retrieval) got a 401 instead of an answer.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from model.eval import agent_client

pytestmark = pytest.mark.unit


def _transport(handler):
    return httpx.MockTransport(handler)


def test_ask_live_agent_requires_a_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GATEWAY_KEY"):
        asyncio.run(agent_client.ask_live_agent("http://runtime", "agent-1", "tenant-1", "hi?"))


def test_ask_live_agent_sends_the_headers_the_runtime_requires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_KEY", "secret-key")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b'data: "Hello"\ndata: " world"\ndata: [DONE]\n')

    real_async_client = httpx.AsyncClient

    def fake_async_client(*_: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_async_client(transport=_transport(handler))

    monkeypatch.setattr(agent_client.httpx, "AsyncClient", fake_async_client)

    answer = asyncio.run(agent_client.ask_live_agent("http://runtime", "agent-1", "tenant-1", "hi?"))

    assert answer == "Hello world"
    assert seen["headers"]["x-kong-verified"] == "true"
    assert seen["headers"]["x-gateway-key"] == "secret-key"
    assert seen["headers"]["x-tenant-id"] == "tenant-1"
    assert seen["body"] == {"query": "hi?", "agent_id": "agent-1"}


def test_the_default_role_is_one_kb_backend_actually_grants_retrieval_to(monkeypatch: pytest.MonkeyPatch) -> None:
    # "admin" canonicalises to "ADMIN" in kb-backend's RolePermissions lookup
    # (services/kb_backend/dependencies/auth.py's canonical_role), which is not
    # one of the four seeded roles — every retrieval got a bare 403 until this
    # was set to a role the catalog actually grants retrieveKnowledge to
    # (services/kb_backend/dependencies/permissions.py).
    monkeypatch.setenv("GATEWAY_KEY", "secret-key")
    monkeypatch.delenv("EVAL_USER_ROLES", raising=False)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, content=b'data: "ok"\ndata: [DONE]\n')

    real_async_client = httpx.AsyncClient

    def fake_async_client(*_: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_async_client(transport=_transport(handler))

    monkeypatch.setattr(agent_client.httpx, "AsyncClient", fake_async_client)

    asyncio.run(agent_client.ask_live_agent("http://runtime", "agent-1", "tenant-1", "hi?"))

    assert seen["headers"]["x-user-roles"] == "platform-admin"


def test_ask_live_agent_accepts_the_legacy_kb_gateway_key_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.setenv("KB_GATEWAY_KEY", "legacy-key")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, content=b'data: "ok"\ndata: [DONE]\n')

    real_async_client = httpx.AsyncClient

    def fake_async_client(*_: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_async_client(transport=_transport(handler))

    monkeypatch.setattr(agent_client.httpx, "AsyncClient", fake_async_client)

    asyncio.run(agent_client.ask_live_agent("http://runtime", "agent-1", "tenant-1", "hi?"))

    assert seen["headers"]["x-gateway-key"] == "legacy-key"
