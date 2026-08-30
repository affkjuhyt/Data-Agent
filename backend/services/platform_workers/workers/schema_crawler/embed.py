"""Text Description embedding (doc §2.5) — BGE-M3 via LiteLLM, the same
endpoint and EMBEDDING_DIM convention as workflow_memory.py and the memory
worker, so the schema_catalog collection lives in the same vector space as the
rest of the platform's embeddings (default EMBEDDING_DIM=1024 matches BGE-M3).

The HTTP call itself lives in libs.shared.embeddings; this module keeps the
one-argument ``embed(text)`` the crawl loop and its tests patch.
"""
from __future__ import annotations

from services.platform_workers.common.settings import LiteLLMSettings

from libs.shared.embeddings import embed_one

_litellm = LiteLLMSettings()
LITELLM_BASE = _litellm.base_url
LITELLM_KEY = _litellm.api_key


def embed(text: str) -> list[float]:
    return embed_one(text, base_url=LITELLM_BASE, api_key=LITELLM_KEY, timeout=30)
