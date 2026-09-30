from typing import Any

from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.data import (
    _text_to_sql_handler,
)
from services.workflow_runtime.domain.state import AgentState


async def db_connector_node(state: AgentState) -> dict[str, Any]:
    """Retrieval node độc lập, đứng ngang hàng kb_search/mcp dưới Planner —
    kéo vào canvas là dùng được ngay, không cần bật thêm gì ở Tool Executor.
    Toàn bộ logic (đọc schema, chọn DB khi có nhiều kết nối, sinh SQL, chạy
    song song) nằm trong _text_to_sql_handler đã sửa — node này chỉ chuyển
    tiếp query và map kết quả vào state."""
    cfg = state.get("config", {})
    # Slots are per-TURN state, not per-agent config, so they are overlaid
    # here rather than stored in cfg. The handler needs them to compile a
    # follow-up ("chỉ lấy miền Bắc") as the previous request plus a filter.
    cfg = {**cfg, "slots": state.get("slots")}
    call = next((tc for tc in state.get("tool_calls", []) if tc.get("type") == "db_query"), None)
    query = (call or {}).get("query") or state["query"]
    results, errors = await _text_to_sql_handler({"query": query}, cfg)
    return {
        "db_results": results or [],
        "db_error": errors[0]["error"] if errors else None,
    }
