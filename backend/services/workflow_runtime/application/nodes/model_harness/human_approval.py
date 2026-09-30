import json
import logging
from typing import Any

from langgraph.types import interrupt

from services.workflow_runtime.domain.state import AgentState

logger = logging.getLogger(__name__)


def _build_payload(state: AgentState) -> dict[str, Any]:
    """Tự động generate payload dựa trên state hiện tại — không cần biết node trước là gì."""
    query = state.get("query", "")

    if state.get("response"):
        return {
            "title": "Xem xét câu trả lời trước khi gửi",
            "description": state["response"][:400],
            "detail": [
                {"source": c.get("source", ""), "content": c.get("content", "")[:200]}
                for c in state.get("reranked_chunks", [])[:3]
            ],
        }

    if state.get("reranked_chunks"):
        chunks = state["reranked_chunks"]
        return {
            "title": "Xác nhận context trước khi trả lời",
            "description": f"{len(chunks)} chunks đã rerank từ KB/MCP",
            "detail": [
                {"source": c.get("source", ""), "content": c.get("content", "")[:200]}
                for c in chunks[:3]
            ],
        }

    if state.get("tool_results"):
        results = state["tool_results"]
        return {
            "title": "Xác nhận kết quả thực thi tool",
            "description": f"{len(results)} kết quả từ tool executor",
            "detail": results[:3],
        }

    if state.get("rrf_results"):
        results = state["rrf_results"]
        return {
            "title": "Xác nhận kết quả sau RRF ranking",
            "description": f"{len(results)} items đã merge và rank",
            "detail": [{"source": r.get("source", ""), "score": r.get("rrf_score")} for r in results[:5]],
        }

    if state.get("kb_chunks") or state.get("mcp_results"):
        n_kb  = len(state.get("kb_chunks", []))
        n_mcp = len(state.get("mcp_results", []))
        return {
            "title": "Xác nhận kết quả tìm kiếm",
            "description": f"{n_kb} KB chunks · {n_mcp} MCP results",
            "detail": {
                "kb":  state.get("kb_chunks",   [])[:3],
                "mcp": state.get("mcp_results", [])[:3],
            },
        }

    if state.get("tool_calls"):
        calls = state["tool_calls"]
        types = {tc.get("type", "") for tc in calls}
        if "tool" in types:
            title = f"Xác nhận thực thi {len(calls)} tool call(s)"
        elif "kb_search" in types:
            title = "Xác nhận tìm kiếm Knowledge Base"
        elif "mcp" in types:
            title = "Xác nhận gọi MCP endpoint"
        else:
            title = "Xác nhận kế hoạch của planner"
        desc = "; ".join(
            f"{c.get('tool_name', c.get('type','?'))}({json.dumps(c.get('arguments', {}), ensure_ascii=False)[:80]})"
            for c in calls[:3]
        )
        return {"title": title, "description": desc, "detail": calls}

    return {
        "title": "Xác nhận tiếp tục",
        "description": query,
        "detail": {},
    }


def human_approval_node(state: AgentState) -> dict[str, Any]:
    payload = _build_payload(state)
    payload["query"] = state.get("query", "")

    logger.info("human_approval | waiting for review — title=%r", payload["title"])

    decision = interrupt(payload)

    approved = bool(decision.get("approved", False))
    note     = decision.get("note", "")

    logger.info("human_approval | decision=%s note=%r", approved, note)

    if not approved:
        return {
            "human_approved": False,
            "human_note":     note,
            "response": note or "Yêu cầu của bạn chưa được phê duyệt. Vui lòng liên hệ trực tiếp để được hỗ trợ.",
        }

    return {"human_approved": True, "human_note": note}
