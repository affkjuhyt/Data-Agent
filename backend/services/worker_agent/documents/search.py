"""The model-side services of document retrieval (tab 5, UC-05-04 steps 2 and 4).

Dense neighbours come from the Qdrant collections worker-document writes (one per document
collection, one point per chunk, point id = chunk id) with the query embedded by the same bge-m3
endpoint; the reranker is the TEI cross-encoder (ADR-35). Both are optional: without them the
retriever ranks on keywords alone, and a failure of either costs recall, never the answer.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

import httpx
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from libs import vectors

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    qdrant_collection: str
    embed_model: str


class SearchServices:
    def __init__(
        self,
        *,
        qdrant_url: str | None,
        embed_url: str | None,
        embed_key: str = "",
        embed_route: str = "embedder",
        rerank_url: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        # The chunks' vectors through libs/vectors (qdrant-client), the layout worker-document
        # writes; the client is made once and kept, its connections with it.
        self._qdrant = vectors.connect(qdrant_url, timeout=timeout) if qdrant_url else None
        self._embed = embed_url.rstrip("/") if embed_url else None
        self._key = embed_key
        self._route = embed_route
        self._rerank = rerank_url.rstrip("/") if rerank_url else None
        self._timeout = timeout

    @property
    def reranks(self) -> bool:
        return self._rerank is not None

    @property
    def dense_configured(self) -> bool:
        return self._qdrant is not None and self._embed is not None

    async def dense(
        self, targets: list[Target], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        """(chunk id, cosine) best first, over the collections' points of their current model."""
        if self._qdrant is None or not self._embed or not targets:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self._embed}/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": self._route, "input": [query]},
                )
                r.raise_for_status()
                vector = r.json()["data"][0]["embedding"]
            # BR-18: each collection is read for the points of its own current model only.
            return await vectors.nearest(
                self._qdrant,
                [vectors.Target(t.qdrant_collection, t.embed_model) for t in targets],
                vector,
                limit,
            )
        except (
            httpx.HTTPError,
            ResponseHandlingException,
            UnexpectedResponse,
            KeyError,
            IndexError,
            ValueError,
        ):
            log.warning("dense document search unavailable", exc_info=True)
            return []

    async def rerank(self, query: str, texts: list[str]) -> list[float] | None:
        """One relevance score per text, in input order; None when the reranker is unavailable."""
        if not self._rerank or not texts:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self._rerank}/rerank",
                    json={"query": query, "texts": texts, "truncate": True},
                )
                r.raise_for_status()
                scores = [0.0] * len(texts)
                for item in r.json():
                    scores[int(item["index"])] = float(item["score"])
                return scores
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            log.warning("document reranker unavailable", exc_info=True)
            return None


def services_from_env() -> SearchServices:
    return SearchServices(
        qdrant_url=os.getenv("QDRANT_URL") or None,
        # The endpoint worker-document embedded the chunks with (DOCUMENT_EMBED_URL, else LiteLLM).
        embed_url=os.getenv("DOCUMENT_EMBED_URL") or os.getenv("LITELLM_BASE_URL") or None,
        embed_key=os.getenv("LITELLM_API_KEY", ""),
        embed_route=os.getenv("DOCUMENT_EMBED_ROUTE", "embedder"),
        rerank_url=os.getenv("DOCUMENT_RERANK_URL") or None,
    )
