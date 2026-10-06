import logging
from typing import Any

from services.workflow_runtime.domain.state import AgentState

logger = logging.getLogger(__name__)

_K = 60


def rrf_ranking_node(state: AgentState) -> dict[str, Any]:
    """Fuse the TEXT sources only. Query results (`result_sets`) are not
    candidates: a table is not a better or worse passage than a document, and
    ranking it against them is how one used to lose its place in the top-N."""
    kb_chunks    = state.get("kb_chunks", [])
    mcp_results  = state.get("mcp_results", [])
    tool_results = state.get("tool_results", [])
    all_items    = kb_chunks + mcp_results + tool_results

    if not all_items:
        return {"rrf_results": []}

    scores:  dict[str, float]      = {}
    meta:    dict[str, dict[str, Any]]       = {}
    sources: dict[str, list[str]]  = {}

    for source_name, items in [("kb", kb_chunks), ("mcp", mcp_results), ("tool", tool_results)]:
        for rank, item in enumerate(items, start=1):
            item_id = item.get("id")
            if not item_id:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (_K + rank)
            meta.setdefault(item_id, item)
            sources.setdefault(item_id, []).append(source_name)

    top_n = state["config"].get("rrf_top_n", 20)
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    results = [
        {**meta[i], "rrf_score": scores[i], "rrf_rank": rrf_rank, "rrf_sources": sources[i]}
        for rrf_rank, i in enumerate(sorted_ids[:top_n], start=1)
    ]

    logger.info("rrf_ranking | merged %d items → top %d", len(all_items), len(results))
    return {"rrf_results": results}
