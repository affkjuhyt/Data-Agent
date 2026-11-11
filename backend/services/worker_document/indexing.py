"""Vectors of a document's chunks in Qdrant (UC-05-02 step 7).

One Qdrant collection per document collection (``collection.qdrant_collection``), one point per
chunk with the chunk id as point id, so ``chunk.qdrant_point_id`` is the chunk's own id and the
API maps a neighbour back to Postgres without a second lookup. Every point carries the embedding
model it was made with: the search side keeps only points of the collection's current model
(BR-18), so a re-index to a new model can be built alongside without mixing the two.
"""

from __future__ import annotations

import functools
import logging
import os
import uuid
from dataclasses import dataclass

import httpx
from qdrant_client import AsyncQdrantClient

from libs import vectors
from libs.shared.embeddings import DEFAULT_MODEL, aembed

log = logging.getLogger(__name__)
EMBED_BATCH = 32


@functools.cache
def _qdrant(url: str) -> AsyncQdrantClient:
    # One client for the process: a document's indexing reuses its connections, and none is left
    # open per document (the httpx client of the embeddings call is closed by its caller).
    return vectors.connect(url)


@dataclass(frozen=True)
class Point:
    chunk_id: uuid.UUID
    text: str


class VectorIndex:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        qdrant: AsyncQdrantClient,
        embed_url: str,
        embed_key: str,
        embed_route: str = DEFAULT_MODEL,
        dimension: int = 1024,
    ) -> None:
        self._client = client
        # Written through libs/vectors (qdrant-client), the layout the two searches read.
        self._qdrant = qdrant
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
            qdrant=_qdrant(qdrant),
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
        if await vectors.ensure_collection(self._qdrant, collection, self._dimension):
            log.info("qdrant collection created name=%s dim=%d", collection, self._dimension)
        await vectors.delete_chunks(self._qdrant, collection, deletes)
        for start in range(0, len(upserts), EMBED_BATCH):
            batch = upserts[start : start + EMBED_BATCH]
            embedded = await aembed(
                [p.text for p in batch],
                base_url=self._embed_url,
                api_key=self._embed_key,
                model=self._route,
                client=self._client,
            )
            await vectors.upsert_chunks(
                self._qdrant,
                collection,
                [
                    vectors.ChunkPoint(
                        p.chunk_id,
                        vector,
                        {
                            "org_id": str(org_id),
                            "collection_id": str(collection_id),
                            "document_id": str(document_id),
                            "chunk_id": str(p.chunk_id),
                            "embed_model": embed_model,
                        },
                    )
                    for p, vector in zip(batch, embedded, strict=True)
                ],
            )
