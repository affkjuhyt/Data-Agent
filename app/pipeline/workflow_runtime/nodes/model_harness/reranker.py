import logging
import os
import litellm

from app.pipeline.workflow_runtime.state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")


def reranker_node(state: AgentState) -> dict:
    rrf_results = state.get("rrf_results", [])
    if not rrf_results:
        return {"reranked_chunks": []}

    rrf_results = [item for item in rrf_results if item.get("content")]
    documents = [item["content"] for item in rrf_results]
    top_n = min(state["config"].get("reranker_top_n", 5), len(documents))

    try:
        response = litellm.rerank(
            model=state["config"].get("reranker_model", "reranker"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            query=state["query"],
            documents=documents,
            top_n=top_n,
        )
        reranked = sorted(response.results, key=lambda x: x.relevance_score, reverse=True)
        top_chunks = [
            {**rrf_results[r.index], "rerank_score": r.relevance_score, "rerank_rank": rank}
            for rank, r in enumerate(reranked)
        ]
        logger.info("reranker | reranked %d → top %d", len(rrf_results), len(top_chunks))
        return {"reranked_chunks": top_chunks}
    except Exception as e:
        logger.warning("reranker | failed: %s — using rrf order as fallback", e)
        return {"reranked_chunks": rrf_results[:top_n]}
