import asyncio
import logging
import uuid
from typing import Any

from services.workflow_runtime.domain.state import AgentState
from services.workflow_runtime.infrastructure.mcp_client import MCPClientError, MCPRegistry

logger = logging.getLogger(__name__)
_REGISTRY = MCPRegistry()


def _endpoint_for(call: dict[str, Any], endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    server_id = str(call.get("server_id") or "")
    return next(
        (
            endpoint
            for endpoint in endpoints
            if server_id
            and server_id
            in {
                str(endpoint.get("server_id") or ""),
                str(endpoint.get("id") or ""),
                str(endpoint.get("name") or ""),
            }
        ),
        None,
    )


async def _execute_call(
    registry: MCPRegistry, call: dict[str, Any], endpoints: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    tool = str(call.get("tool") or call.get("tool_name") or "")
    endpoint = _endpoint_for(call, endpoints)
    if not endpoint or not tool:
        return [], {"code": "TOOL_UNAVAILABLE", "server": call.get("server_id"), "tool": tool}
    try:
        content = await registry.call_tool(endpoint, tool, call.get("arguments") or {})
        return [
            {
                "id": str(uuid.uuid4()),
                "content": content,
                "source": endpoint.get("name") or call.get("server_id") or "mcp",
            }
        ], None
    except MCPClientError as exc:
        logger.warning("mcp | tool=%s server=%s failed code=%s", tool, call.get("server_id"), exc.code)
        return [], {
            "code": exc.code,
            "server": endpoint.get("name") or call.get("server_id"),
            "tool": tool,
            "detail": exc.detail,
        }
    except Exception:
        logger.exception("mcp | tool=%s server=%s failed", tool, call.get("server_id"))
        return [], {
            "code": "TOOL_UNAVAILABLE",
            "server": endpoint.get("name") or call.get("server_id"),
            "tool": tool,
            "detail": "MCP tool call failed.",
        }


async def mcp_node(state: AgentState) -> dict[str, Any]:
    mcp_endpoints = state["config"].get("mcp_endpoints", [])
    if not mcp_endpoints:
        return {"mcp_results": [], "mcp_errors": []}

    mcp_calls = [tc for tc in state.get("tool_calls", []) if tc.get("type") == "mcp"]
    if not mcp_calls:
        return {"mcp_results": [], "mcp_errors": []}

    outcomes = await asyncio.gather(
        *[_execute_call(_REGISTRY, call, mcp_endpoints) for call in mcp_calls]
    )
    all_results = [item for results, _ in outcomes for item in results]
    all_errors = [err for _, err in outcomes if err]
    return {"mcp_results": all_results, "mcp_errors": all_errors}
