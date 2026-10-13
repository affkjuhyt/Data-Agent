"""Validated MCP inputs carried by a worker-agent plan.

The planner may describe tools, but it must not be allowed to invent one.  This
module keeps the boundary pure: no network, database, or credential access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MCPPlanError(ValueError):
    """A structured refusal for a tool that is not in the validated plan."""

    code = "TOOL_UNAVAILABLE"

    def __init__(self, server_id: str, tool: str, detail: str) -> None:
        super().__init__(detail)
        self.server_id = server_id
        self.tool = tool
        self.detail = detail


@dataclass(frozen=True)
class MCPTool:
    server_id: str
    tool: str
    description: str
    input_schema: dict[str, Any]


def catalog_from_tools(tools: list[dict[str, Any]]) -> list[MCPTool]:
    """Convert discovered tool descriptions into the planner-safe shape."""
    return [
        MCPTool(
            server_id=str(item.get("server_id") or ""),
            tool=str(item.get("tool") or ""),
            description=str(item.get("description") or ""),
            input_schema=dict(item.get("input_schema") or {}),
        )
        for item in tools
        if item.get("server_id") and item.get("tool")
    ]


def prompt_section(tools: list[MCPTool]) -> str:
    """Render a bounded, credential-free planner section."""
    if not tools:
        return "MCP TOOLS: none"
    lines = ["MCP TOOLS:"]
    for item in tools:
        lines.append(
            f"- {item.server_id}/{item.tool}: {item.description} "
            f"schema={item.input_schema}"
        )
    return "\n".join(lines)


def validate_tool_calls(
    calls: list[dict[str, Any]], tools: list[MCPTool]
) -> list[dict[str, Any]]:
    """Validate and normalize plan tool calls before any network call."""
    allowed = {(item.server_id, item.tool) for item in tools}
    normalized: list[dict[str, Any]] = []
    for call in calls:
        server_id = str(call.get("server_id") or "")
        tool = str(call.get("tool") or "")
        if (server_id, tool) not in allowed:
            raise MCPPlanError(
                server_id,
                tool,
                f"MCP tool '{tool}' is not available on server '{server_id}'.",
            )
        arguments = call.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise MCPPlanError(server_id, tool, "MCP tool arguments must be an object.")
        normalized.append({"server_id": server_id, "tool": tool, "arguments": arguments})
    return normalized
