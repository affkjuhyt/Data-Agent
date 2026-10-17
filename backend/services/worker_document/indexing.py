"""Vectors of a document's chunks in Qdrant (UC-05-02 step 7).

One Qdrant collection per document collection (``collection.qdrant_collection``), one point per
chunk with the chunk id as point id, so ``chunk.qdrant_point_id`` is the chunk's own id and the
API maps a neighbour back to Postgres without a second lookup. Every point carries the embedding
model it was made with: the search side keeps only points of the collection's current model
(BR-18), so a re-index to a new model can be built alongside without mixing the two.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

import httpx

from libs.shared.embeddings import DEFAULT_MODEL, aembed

log = logging.getLogger(__name__)
EMBED_BATCH = 32


@dataclass(frozen=True)
class Point:
    chunk_id: uuid.UUID
    text: str


class VectorIndex:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        qdrant_url: str,
        embed_url: str,
        embed_key: str,
        embed_route: str = DEFAULT_MODEL,
        dimension: int = 1024,
    ) -> None:
        self._client = client
        self._qdrant = qdrant_url.rstrip("/")
        self._embed_url = embed_url
        self._embed_key = embed_key
        self._route = embed_route
        self._dimension = dimension

    @classmethod
    def from_env(cls, client: httpx.AsyncClient) -> VectorIndex | None:
        qdrant = os.getenv("QDRANT_URL", "").strip()
        # DOCUMENT_EMBED_URL: an OpenAI-compatible embeddings server reached directly (the local
        # cluster runs no LiteLLM and talks to TEI); otherwise the LiteLLM gateway.
        embed = (os.getenv("DOCUMENT_EMBED_URL") or os.getenv("LITELLM_BASE_URL") or "").strip()
        if not qdrant or not embed:
            return None
        return cls(
            client,
            qdrant_url=qdrant,
            embed_url=embed,
            embed_key=os.getenv("LITELLM_API_KEY", ""),
            # The LiteLLM route that serves collection.embed_model (bge-m3 behind `embedder`).
            embed_route=os.getenv("DOCUMENT_EMBED_ROUTE", DEFAULT_MODEL),
            dimension=int(os.getenv("EMBEDDING_DIM", "1024")),
        )

    async def sync(
        self,
        collection: str,
        *,
        org_id: uuid.UUID,
        collection_id: uuid.UUID,
        document_id: uuid.UUID,
        embed_model: str,
        upserts: list[Point],
        deletes: list[uuid.UUID],
    ) -> None:
        """Writes the points of new chunks and deletes those of chunks the document no longer
        has. Chunks kept from an earlier run keep their points: re-processing a document embeds
        only what changed (AC-05-02-05)."""
        await self._ensure(collection)
        if deletes:
            await self._call(
                "POST",
                f"/collections/{collection}/points/delete?wait=true",
                {"points": [str(i) for i in deletes]},
            )
        for start in range(0, len(upserts), EMBED_BATCH):
            batch = upserts[start : start + EMBED_BATCH]
            vectors = await aembed(
                [p.text for p in batch],
                base_url=self._embed_url,
                api_key=self._embed_key,
                model=self._route,
                client=self._client,
            )
            await self._call(
                "PUT",
                f"/collections/{collection}/points?wait=true",
                {
                    "points": [
                        {
                            "id": str(p.chunk_id),
                            "vector": vector,
                            "payload": {
                                "org_id": str(org_id),
                                "collection_id": str(collection_id),
                                "document_id": str(document_id),
                                "chunk_id": str(p.chunk_id),
                                "embed_model": embed_model,
                            },
                        }
                        for p, vector in zip(batch, vectors, strict=True)
                    ]
                },
            )

    async def _ensure(self, collection: str) -> None:
        response = await self._client.get(f"{self._qdrant}/collections/{collection}")
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()
        await self._call(
            "PUT",
            f"/collections/{collection}",
            {"vectors": {"size": self._dimension, "distance": "Cosine"}},
        )
        await self._call(
            "PUT",
            f"/collections/{collection}/index",
            {"field_name": "document_id", "field_schema": "keyword"},
        )
        log.info("qdrant collection created name=%s dim=%d", collection, self._dimension)

    async def _call(self, method: str, path: str, body: dict) -> None:
        response = await self._client.request(method, f"{self._qdrant}{path}", json=body)
        response.raise_for_status()
