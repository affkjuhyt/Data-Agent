from unittest.mock import AsyncMock

import pytest

from services.workflow_runtime.application.nodes.sandbox import mcp as mcp_module
from services.workflow_runtime.infrastructure.mcp_client import MCPClientError
from services.workflow_runtime.infrastructure.mcp_client import MCPRegistry


class _Registry:
    call_tool = AsyncMock(return_value="42")


@pytest.mark.asyncio
async def test_mcp_node_uses_server_id_and_returns_text_result(monkeypatch):
    registry = _Registry()
    monkeypatch.setattr(mcp_module, "_REGISTRY", registry)

    result = await mcp_module.mcp_node(
        {
            "query": "q",
            "tool_calls": [
                {"type": "mcp", "server_id": "srv-1", "tool": "lookup", "arguments": {}}
            ],
            "config": {"mcp_endpoints": [{"server_id": "srv-1", "name": "catalog", "url": "https://mcp"}]},
        }
    )

    assert result["mcp_results"][0]["content"] == "42"
    assert result["mcp_results"][0]["source"] == "catalog"
    registry.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_node_exposes_structured_tool_unavailable_error(monkeypatch):
    registry = _Registry()
    registry.call_tool = AsyncMock(side_effect=MCPClientError("TOOL_UNAVAILABLE", "not reachable"))
    monkeypatch.setattr(mcp_module, "_REGISTRY", registry)

    result = await mcp_module.mcp_node(
        {
            "query": "q",
            "tool_calls": [
                {"type": "mcp", "server_id": "srv-1", "tool": "lookup", "arguments": {}}
            ],
            "config": {"mcp_endpoints": [{"server_id": "srv-1", "name": "catalog", "url": "https://mcp"}]},
        }
    )

    assert result["mcp_results"] == []
    assert result["mcp_errors"] == [
        {"code": "TOOL_UNAVAILABLE", "server": "catalog", "tool": "lookup", "detail": "not reachable"}
    ]


@pytest.mark.asyncio
async def test_registry_rejects_tool_outside_endpoint_allowlist_before_network_call():
    with pytest.raises(MCPClientError, match="not allowed"):
        await MCPRegistry().call_tool(
            {"server_id": "srv-1", "url": "https://mcp", "allowed_tools": ["lookup"]},
            "delete_all",
            {},
        )
