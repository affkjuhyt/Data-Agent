import logging
from app.pipeline.workflow_runtime.state import AgentState

logger = logging.getLogger(__name__)

_K = 60


def rrf_ranking_node(state: AgentState) -> dict:
    kb_chunks = state.get("kb_chunks", [])
    mcp_results = state.get("mcp_results", [])
    all_items = kb_chunks + mcp_results

    if not all_items:
        return {"rrf_results": []}

    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}
    sources: dict[str, list[str]] = {}

    for rank, item in enumerate(kb_chunks, start=1):
        item_id = item.get("id")
        if not item_id:
            continue
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (_K + rank)
        meta.setdefault(item_id, item)
        sources.setdefault(item_id, []).append("kb")

    for rank, item in enumerate(mcp_results, start=1):
        item_id = item.get("id")
        if not item_id:
            continue
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (_K + rank)
        meta.setdefault(item_id, item)
        sources.setdefault(item_id, []).append("mcp")

    top_n = state["config"].get("rrf_top_n", 20)
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    top_20 = [
        {**meta[i], "rrf_score": scores[i], "rrf_rank": rrf_rank, "rrf_sources": sources[i]}
        for rrf_rank, i in enumerate(sorted_ids[:top_n], start=1)
    ]

    logger.info("rrf_ranking | merged %d items → top %d", len(all_items), len(top_20))
    return {"rrf_results": top_20}
