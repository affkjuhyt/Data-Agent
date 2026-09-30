"""Tests workflow_memory._embed()'s actual HTTP call/response-parsing path —
previously this was always monkeypatched away in other tests, so the real
httpx request/response handling was never exercised. `_embed` now delegates
to `libs.shared.embeddings.aembed_one`, which still speaks httpx underneath,
so `respx` intercepts the request at the transport layer: no real socket, no
real LLM server, no API key — just asserts our code builds the right request,
parses the right response shape, and degrades to ``None`` on any failure.
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
import respx

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
@respx.mock
async def test_embed_parses_successful_response():
    import workflow_memory as wm

    route = respx.post(f"{wm._LITELLM_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    )

    vector = await wm._embed("doanh thu quý 1")

    assert route.called
    sent_body = route.calls[0].request.read()
    assert b"doanh thu qu" in sent_body  # request actually carries our text
    assert vector == [0.1, 0.2, 0.3]


@pytest.mark.unit
@respx.mock
async def test_embed_returns_none_on_http_error():
    import workflow_memory as wm

    respx.post(f"{wm._LITELLM_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )

    vector = await wm._embed("doanh thu quý 1")

    assert vector is None


@pytest.mark.unit
@respx.mock
async def test_embed_returns_none_on_malformed_response():
    import workflow_memory as wm

    respx.post(f"{wm._LITELLM_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )

    vector = await wm._embed("doanh thu quý 1")

    assert vector is None
