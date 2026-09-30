import logging
import os
from typing import Any

import httpx

from services.workflow_runtime.domain.state import AgentState

logger = logging.getLogger(__name__)

# Rerank runs on model_service's local cross-encoder (/rerank). litellm.rerank has no
# provider for the local setup ("Unsupported provider: openai"), so call it directly.
_MODEL_SERVICE = os.environ.get("MODEL_SERVICE_URL", "http://model_service:8230").rstrip("/")


def reranker_node(state: AgentState) -> dict[str, Any]:
    rrf_results = state.get("rrf_results", [])
    if not rrf_results:
        return {"reranked_chunks": []}

    rrf_results = [item for item in rrf_results if item.get("content")]
    documents = [item["content"] for item in rrf_results]
    if not documents:
        return {"reranked_chunks": []}
    top_n = min(state["config"].get("reranker_top_n", 5), len(documents))

    try:
        resp = httpx.post(
            f"{_MODEL_SERVICE}/rerank",
            json={"query": state["query"], "documents": documents, "top_n": top_n},
            timeout=30.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        top_chunks = [
            {**rrf_results[r["index"]], "rerank_score": r["relevance_score"], "rerank_rank": rank}
            for rank, r in enumerate(results)
        ]
        logger.info("reranker | reranked %d → top %d", len(rrf_results), len(top_chunks))
        return {"reranked_chunks": top_chunks}
    except Exception as e:
        # rerank unavailable — fall back to the RRF order so retrieval still works.
        logger.warning("reranker | failed: %s — using rrf order as fallback", e)
        return {"reranked_chunks": rrf_results[:top_n]}
