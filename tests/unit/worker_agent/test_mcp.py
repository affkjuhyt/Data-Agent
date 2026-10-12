"""MCP belongs to worker-agent; credentials must not enter cached tool/state data."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.worker_agent.tools.mcp import MCPClientError, MCPRegistry, execute_calls
from services.worker_agent.tools.plan import (
    MCPPlanError,
    MCPTool,
    prompt_section,
    validate_tool_calls,
)

pytestmark = [pytest.mark.unit]


@dataclass
class Tool:
    name: str
    description: str = ""
    inputSchema: dict[str, object] | None = None


@dataclass
class Result:
    content: list[object]
    isError: bool = False


@dataclass
class Text:
    type: str = "text"
    text: str = "ok"


class Transport:
    def __init__(self, streams: tuple[object, object]) -> None:
        self.streams = streams
        self.headers: dict[str, str] | None = None

    async def __aenter__(self) -> tuple[object, object, None]:
        return (*self.streams, None)

    async def __aexit__(self, *_: object) -> None:
        return None


class Session:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = tools

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> object:
        return type("Tools", (), {"tools": self.tools})()

    async def call_tool(self, name: str, arguments: dict[str, object]) -> Result:
        assert name == "lookup"
        assert arguments == {"q": "sales"}
        return Result([Text(text="42")])


def _registry() -> MCPRegistry:
    def transport(url: str, headers: dict[str, str]) -> Transport:
        assert url == "https://mcp.example.test"
        assert headers == {"Authorization": "Bearer transient"}
        return Transport((object(), object()))

    return MCPRegistry(
        token_resolver=lambda ref: _token(ref),
        session_factory=transport,
        client_session=lambda _read, _write: Session(
            [Tool("lookup", inputSchema={"type": "object"})]
        ),
    )


async def _token(ref: str) -> str:
    assert ref == "secret/tenants/org/mcp"
    return "transient"


async def test_discovery_is_allow_listed_and_cached() -> None:
    registry = _registry()
    endpoint = {
        "server_id": "server-1",
        "url": "https://mcp.example.test",
        "api_key_ref": "secret/tenants/org/mcp",
        "allowed_tools": ["lookup"],
    }
    first = await registry.list_tools(endpoint)
    second = await registry.list_tools(endpoint)
    assert (
        first
        == second
        == [
            {
                "server_id": "server-1",
                "server_name": "server-1",
                "tool": "lookup",
                "description": "",
                "input_schema": {"type": "object"},
            }
        ]
    )
    assert "transient" not in repr(first)


async def test_disallowed_tool_never_calls_server() -> None:
    registry = _registry()
    with pytest.raises(MCPClientError, match="not allowed"):
        await registry.call_tool(
            {
                "server_id": "server-1",
                "url": "https://mcp.example.test",
                "allowed_tools": ["other"],
            },
            "lookup",
            {},
        )


async def test_execute_calls_returns_safe_error_frame() -> None:
    results, errors = await execute_calls(
        _registry(),
        [{"server_id": "missing", "tool": "lookup", "arguments": {}}],
        [],
    )
    assert results == []
    assert errors == [{"code": "TOOL_UNAVAILABLE", "server": "missing", "tool": "lookup"}]


def test_plan_rejects_unknown_tool_without_fallback() -> None:
    with pytest.raises(MCPPlanError, match="not available"):
        validate_tool_calls(
            [{"server_id": "server-1", "tool": "legacy_execute", "arguments": {}}],
            [MCPTool("server-1", "lookup", "Lookup", {"type": "object"})],
        )


def test_plan_prompt_contains_schema_but_not_credentials() -> None:
    section = prompt_section(
        [MCPTool("server-1", "lookup", "Lookup", {"type": "object"})]
    )
    assert "server-1/lookup" in section
    assert "type" in section
    assert "Bearer" not in section


async def test_legacy_execute_endpoint_is_rejected() -> None:
    registry = _registry()
    with pytest.raises(MCPClientError, match="Legacy MCP"):
        await registry.list_tools(
            {
                "server_id": "server-1",
                "url": "https://mcp.example.test/execute",
                "allowed_tools": ["lookup"],
            }
        )
