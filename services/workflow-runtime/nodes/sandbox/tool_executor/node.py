import asyncio
import logging
import time

from state import AgentState

from nodes.sandbox.tool_executor.handlers.data import (
    _text_to_sql_handler,
    _text_to_pandas_handler,
)
from nodes.sandbox.tool_executor.handlers.filesystem import (
    _file_read_handler,
    _grep_handler,
    _glob_handler,
    _file_write_handler,
    _file_edit_handler,
)
from nodes.sandbox.tool_executor.handlers.shell import _bash_handler
from nodes.sandbox.tool_executor.handlers.langchain import (
    _web_search_handler,
    _wikipedia_handler,
    _calculator_handler,
)
from nodes.sandbox.tool_executor.handlers.agent import _delegate_to_agent_handler

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, object] = {
    "text_to_sql":       _text_to_sql_handler,
    "text_to_pandas":    _text_to_pandas_handler,
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


async def _run_timed(handler, call: dict, cfg: dict) -> tuple[str, dict | None, dict | None, int]:
    """Run one handler and return (tool_name, result, error, latency_ms)."""
    tool_name = call.get("tool_name", "unknown")
    t0 = time.monotonic()
    try:
        r, e = await handler(call, cfg)
        return tool_name, r, e, int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        return tool_name, None, {"tool_name": tool_name, "error": str(exc)}, int((time.monotonic() - t0) * 1000)


async def tool_executor_node(state: AgentState) -> dict:
    cfg     = state["config"]
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
    call_map: list[dict] = []
    for call in calls:
        handler = TOOL_REGISTRY.get(call.get("tool_name", ""))
        if handler:
            tasks.append(_run_timed(handler, call, cfg))
            call_map.append(call)
        else:
            logger.warning("tool_executor | unknown tool: %s", call.get("tool_name"))

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    results, errors, traces = [], [], []
    for i, outcome in enumerate(outcomes):
        call = call_map[i]
        if isinstance(outcome, Exception):
            tool_name = call.get("tool_name", "unknown")
            errors.append({"tool_name": tool_name, "error": str(outcome)})
            traces.append({"tool_name": tool_name, "query": call.get("query", ""), "arguments": call.get("arguments", {}),
                           "status": "error", "error": str(outcome), "latency_ms": 0})
        else:
            tool_name, r, e, lat = outcome
            if r:
                results.append(r)
            if e:
                errors.append(e)
            traces.append({
                "tool_name":  tool_name,
                "query":      call.get("query", ""),
                "arguments":  call.get("arguments", {}),
                "status":     "error" if e else "success",
                "error":      e.get("error") if e else None,
                "result_id":  r.get("id") if r else None,
                "latency_ms": lat,
            })

    logger.info("tool_executor | %d results, %d errors", len(results), len(errors))
    # _tool_traces is NOT in AgentState — LangGraph ignores it for state merge
    # but it appears in astream_events on_chain_end output for conversation_runner to read
    return {"tool_results": results, "tool_errors": errors, "_tool_traces": traces}
