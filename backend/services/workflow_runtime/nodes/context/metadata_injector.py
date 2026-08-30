from typing import Any
from services.workflow_runtime.state import AgentState


async def metadata_injector_node(state: AgentState) -> dict[str, Any]:
    """Copy metadata tĩnh (đã parse sẵn ở config_loader._extract_node_config,
    key "injected_metadata") từ config vào state — để planner/responder đọc
    qua state["injected_metadata"] thay vì phải tự đọc state["config"].

    ASYNC dù thân hàm không await gì — bắt buộc, giống judge_phase1_node
    (xem graph.py): LangGraph chạy node sync qua threadpool executor, làm
    mất OTEL contextvars mà Langfuse SDK v3 dùng để nối span vào đúng cha.
    """
    cfg = state.get("config", {})
    return {"injected_metadata": cfg.get("injected_metadata") or {}}
