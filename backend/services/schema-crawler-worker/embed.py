"""Text Description embedding (doc §2.5) — BGE-M3, same LiteLLM embeddings
endpoint and EMBEDDING_DIM convention as workflow_memory.py/memory-consumer,
so the schema_catalog collection lives in the same vector space as the rest
of the platform's embeddings (default EMBEDDING_DIM=1024 matches BGE-M3)."""
import os
from typing import cast

import httpx

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")


def embed(text: str) -> list[float]:
    r = httpx.post(
        f"{LITELLM_BASE}/v1/embeddings",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        json={"model": "embedder", "input": text},
        timeout=30,
    )
    r.raise_for_status()
    return cast(list[float], r.json()["data"][0]["embedding"])
