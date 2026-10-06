import asyncio
import logging
import time
from typing import Any, cast

from services.workflow_runtime.application import result_sets
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.agent import (
    _delegate_to_agent_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.code_interpreter import (
    _execute_python_handler,
    _execute_sql_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.data import (
    _text_to_pandas_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.filesystem import (
    _file_edit_handler,
    _file_read_handler,
    _file_write_handler,
    _glob_handler,
    _grep_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.langchain import (
    _calculator_handler,
    _web_search_handler,
    _wikipedia_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers.shell import (
    _bash_handler,
)
from services.workflow_runtime.application.nodes.sandbox.tool_executor.tool_grant import (
    ToolNotGrantedError,
    check_sandbox_tool_granted,
)
from services.workflow_runtime.domain.state import AgentState

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, object] = {
    "text_to_pandas":    _text_to_pandas_handler,
    "execute_python":    _execute_python_handler,
    "execute_sql":       _execute_sql_handler,
    "file_read":         _file_read_handler,
    "grep":              _grep_handler,
    "glob":              _glob_handler,
    "file_write":        _file_write_handler,
    "file_edit":         _file_edit_handler,
    "bash":              _bash_handler,
    "web_search":        _web_search_handler,
    "wikipedia":         _wikipedia_handler,
    "calculator":        _calculator_handler,
    "delegate_to_agent": _delegate_to_agent_handler,
}


async def _run_timed(handler: Any, call: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, list[dict[str, Any]] | dict[str, Any] | None, dict[str, Any] | None, int]:
    """Run one handler and return (tool_name, result, error, latency_ms).
    result có thể là 1 dict (đa số handler) hoặc list[dict] (vd. web_search —
    mỗi kết quả tìm kiếm giữ 1 URL riêng, không gộp thành 1 item)."""
    tool_name = call.get("tool_name", "unknown")
    t0 = time.monotonic()
    try:
        r, e = await handler(call, cfg)
        return tool_name, r, e, int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        return tool_name, None, {"tool_name": tool_name, "error": str(exc)}, int((time.monotonic() - t0) * 1000)


async def tool_executor_node(state: AgentState) -> dict[str, Any]:
    cfg     = state["config"]
    # Slots are per-TURN state, not per-agent config, so they are overlaid
    # here rather than stored in cfg. The handler needs them to compile a
    # follow-up ("chỉ lấy miền Bắc") as the previous request plus a filter.
    cfg     = {**cfg, "slots": state.get("slots")}
    allowed = set(cfg.get("allowed_tools", []))
    if not allowed:
        return {"tool_results": [], "tool_errors": [], "_tool_traces": []}

    calls = [
        tc for tc in state.get("tool_calls", [])
        if tc.get("type") == "tool" and tc.get("tool_name") in allowed
    ]
    if not calls:
        return {"tool_results": [], "tool_errors": [], "_tool_traces": []}

    tasks = []
    call_map: list[dict[str, Any]] = []
    results, errors, traces = [], [], []
    for call in calls:
        tool_name = call.get("tool_name", "")
        handler = TOOL_REGISTRY.get(tool_name)
        if not handler:
            logger.warning("tool_executor | unknown tool: %s", tool_name)
            continue
        try:
            check_sandbox_tool_granted(tool_name, cfg)
        except ToolNotGrantedError as grant_err:
            # doc §1.9 — grant check, independent of and prior to sandbox
            # content validation (security_visitor/sql_guard never run for
            # a call rejected here).
            logger.warning("tool_executor | %s", grant_err)
            errors.append({"tool_name": tool_name, "error": str(grant_err)})
            traces.append({"tool_name": tool_name, "query": call.get("query", ""), "arguments": call.get("arguments", {}),
                           "status": "error", "error": str(grant_err), "result_id": None, "latency_ms": 0})
            continue
        tasks.append(_run_timed(handler, call, cfg))
        call_map.append(call)

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    for i, outcome in enumerate(outcomes):
        call = call_map[i]
        if isinstance(outcome, Exception):
            tool_name = call.get("tool_name", "unknown")
            errors.append({"tool_name": tool_name, "error": str(outcome)})
            traces.append({"tool_name": tool_name, "query": call.get("query", ""), "arguments": call.get("arguments", {}),
                           "status": "error", "error": str(outcome), "latency_ms": 0})
        else:
            # asyncio.gather(..., return_exceptions=True) types each entry as
            # tuple[...] | BaseException — the isinstance(outcome, Exception)
            # branch above already handles the exception case, so here it's
            # always the tuple _run_timed actually returns.
            tool_name, r, e, lat = cast(
                "tuple[str, list[dict[str, Any]] | dict[str, Any] | None, dict[str, Any] | None, int]", outcome
            )
            r_list = r if isinstance(r, list) else ([r] if r else [])
            results.extend(r_list)
            if e:
                errors.append(e)
            traces.append({
                "tool_name":  tool_name,
                "query":      call.get("query", ""),
                "arguments":  call.get("arguments", {}),
                "status":     "error" if e else "success",
                "error":      e.get("error") if e else None,
                "result_id":  r_list[0].get("id") if r_list else None,
                "latency_ms": lat,
            })

    # A tool that returned rows (text_to_pandas over a frame) answers like a
    # query, so it leaves the ranked text sources for the result-set channel.
    tabular = [r for r in results if result_sets.is_tabular(r)]
    texts = [r for r in results if not result_sets.is_tabular(r)]
    captured = await result_sets.capture(
        tabular, tenant_id=cfg.get("tenant_id", "") or "", question=state.get("query", ""),
    ) if tabular else []

    logger.info("tool_executor | %d results (%d tables), %d errors", len(results), len(captured), len(errors))
    # _tool_traces is NOT in AgentState — LangGraph ignores it for state merge
    # but it appears in astream_events on_chain_end output for conversation_runner to read
    return {"tool_results": texts, "result_sets": captured, "tool_errors": errors, "_tool_traces": traces}
