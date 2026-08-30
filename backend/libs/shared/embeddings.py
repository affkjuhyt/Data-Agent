"""LiteLLM embeddings client shared by every service that writes vectors.

One HTTP shape — ``POST {base_url}/v1/embeddings`` with ``{"model", "input"}`` —
used to be copied into memory-consumer, schema-crawler-worker and three
workflow-runtime modules. Each copy drifted in small ways (timeouts, error
handling), and each one had to be found when the endpoint or model name moved.
This module is the single copy; callers pass the connection details in rather
than reading env, so a worker's settings object and a FastAPI app's config can
both feed it.

The default model alias ``embedder`` is a LiteLLM route (BGE-M3 today), so the
vector space is the same for every writer and reader of a collection.
"""
from __future__ import annotations

from typing import Any, cast

import httpx

DEFAULT_MODEL = "embedder"
DEFAULT_TIMEOUT = 30.0


def _payload(texts: list[str], model: str) -> dict[str, Any]:
    return {"model": model, "input": texts}


def _vectors(body: Any, expected: int) -> list[list[float]]:
    """``data[i].embedding`` in request order.

    LiteLLM returns one entry per input, each carrying an ``index``; sort on it
    rather than trusting response order, and refuse a short answer rather than
    silently pairing the wrong vector with a text.
    """
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != expected:
        raise ValueError(
            f"embeddings response carries {len(rows) if isinstance(rows, list) else 'no'} "
            f"vectors for {expected} inputs"
        )
    ordered = sorted(rows, key=lambda r: r.get("index", 0)) if all("index" in r for r in rows) else rows
    return [cast(list[float], r["embedding"]) for r in ordered]


def embed(
    texts: list[str],
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[list[float]]:
    """Embed ``texts`` synchronously. Raises ``httpx.HTTPStatusError`` on a non-2xx."""
    if not texts:
        return []
    r = httpx.post(
        f"{base_url.rstrip('/')}/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json=_payload(texts, model),
        timeout=timeout,
    )
    r.raise_for_status()
    return _vectors(r.json(), len(texts))


async def aembed(
    texts: list[str],
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 — httpx enforces it per request
    client: httpx.AsyncClient | None = None,
) -> list[list[float]]:
    """Async twin of :func:`embed`. Pass ``client`` to reuse a connection pool."""
    if not texts:
        return []
    url = f"{base_url.rstrip('/')}/v1/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    if client is not None:
        r = await client.post(url, headers=headers, json=_payload(texts, model), timeout=timeout)
    else:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            r = await owned.post(url, headers=headers, json=_payload(texts, model))
    r.raise_for_status()
    return _vectors(r.json(), len(texts))


def embed_one(
    text: str,
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[float]:
    """Single-text convenience over :func:`embed`."""
    return embed([text], base_url=base_url, api_key=api_key, model=model, timeout=timeout)[0]


async def aembed_one(
    text: str,
    *,
    base_url: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 — httpx enforces it per request
    client: httpx.AsyncClient | None = None,
) -> list[float]:
    """Single-text convenience over :func:`aembed`."""
    vectors = await aembed(
        [text], base_url=base_url, api_key=api_key, model=model, timeout=timeout, client=client,
    )
    return vectors[0]
