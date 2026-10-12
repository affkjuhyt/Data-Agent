"""MCP Streamable HTTP adapter for worker-agent.

The question worker owns tool execution.  This module deliberately accepts an endpoint
description from the worker's validated plan and resolves a bearer token only for the
outbound request.  Secrets are never returned in tool descriptions, cached values, state,
or logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class MCPClientError(RuntimeError):
    """A safe, user-facing MCP failure with no credential material."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Session(Protocol):
    async def initialize(self) -> Any: ...

    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


SessionFactory = Callable[[str, dict[str, str]], Any]
TokenResolver = Callable[[str], Awaitable[str]]


def _streamable_client(url: str, headers: dict[str, str]) -> Any:
    from mcp.client.streamable_http import streamablehttp_client

    return streamablehttp_client(url, headers=headers)


def _client_session(read_stream: Any, write_stream: Any) -> Any:
    from mcp import ClientSession

    return ClientSession(read_stream, write_stream)


class MCPRegistry:
    """Discover and invoke an allow-listed MCP server over Streamable HTTP."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        cache_ttl: float = 60.0,
        token_resolver: TokenResolver | None = None,
        session_factory: SessionFactory = _streamable_client,
        client_session: Callable[[Any, Any], Any] = _client_session,
    ) -> None:
        if redis_client is not None:
            self._redis = redis_client
        elif os.getenv("REDIS_URL"):
            from redis.asyncio import from_url

            self._redis = from_url(os.environ["REDIS_URL"], decode_responses=True)
        else:
            self._redis = None
        self._cache_ttl = cache_ttl
        self._token_resolver = token_resolver
        self._session_factory = session_factory
        self._client_session = client_session
        self._local_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @staticmethod
    def _endpoint_id(endpoint: dict[str, Any]) -> str:
        return str(endpoint.get("server_id") or endpoint.get("id") or "")

    async def _headers(self, endpoint: dict[str, Any]) -> dict[str, str]:
        ref = str(endpoint.get("api_key_ref") or "")
        if not ref:
            return {}
        if self._token_resolver is None:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint credentials are unavailable.")
        try:
            token = await self._token_resolver(ref)
        except Exception as exc:
            raise MCPClientError(
                "TOOL_UNAVAILABLE", "MCP endpoint credentials are unavailable."
            ) from exc
        if not token:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint credentials are unavailable.")
        return {"Authorization": f"Bearer {token}"}

    async def _cache_read(self, key: str) -> list[dict[str, Any]] | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                return json.loads(raw) if raw else None
            except (RedisError, TypeError, ValueError):
                logger.warning("worker-agent | MCP tool cache unavailable; using local cache")
                self._redis = None
        cached = self._local_cache.get(key)
        return cached[1] if cached and cached[0] > time.monotonic() else None

    async def _cache_write(self, key: str, tools: list[dict[str, Any]]) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(
                    key, json.dumps(tools, ensure_ascii=False), ex=int(self._cache_ttl)
                )
                return
            except (RedisError, TypeError, ValueError):
                logger.warning("worker-agent | MCP tool cache unavailable; using local cache")
                self._redis = None
        self._local_cache[key] = (time.monotonic() + self._cache_ttl, tools)

    async def _with_session(self, endpoint: dict[str, Any]) -> Any:
        endpoint_id = self._endpoint_id(endpoint)
        url = str(endpoint.get("url") or "")
        if not endpoint_id or not url or not url.startswith(("http://", "https://")):
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint is not configured.")
        headers = await self._headers(endpoint)
        try:
            transport = self._session_factory(url, headers)
            streams = await transport.__aenter__()
            session = self._client_session(streams[0], streams[1])
            await session.__aenter__()
            return transport, session
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint is unavailable.") from exc

    async def _close_session(self, transport: Any, session: Any) -> None:
        await session.__aexit__(None, None, None)
        await transport.__aexit__(None, None, None)

    async def list_tools(self, endpoint: dict[str, Any]) -> list[dict[str, Any]]:
        endpoint_id = self._endpoint_id(endpoint)
        if not endpoint_id:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint is not configured.")
        cache_key = f"mcp:tools:{endpoint_id}"
        cached = await self._cache_read(cache_key)
        if cached is not None:
            return cached
        transport, session = await self._with_session(endpoint)
        try:
            await session.initialize()
            response = await session.list_tools()
        except Exception as exc:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP tool discovery failed.") from exc
        finally:
            await self._close_session(transport, session)
        allowed = set(endpoint.get("allowed_tools") or [])
        tools = [
            {
                "server_id": endpoint_id,
                "server_name": str(endpoint.get("name") or endpoint_id),
                "tool": str(tool.name),
                "description": str(tool.description or ""),
                "input_schema": dict(tool.inputSchema or {}),
            }
            for tool in response.tools
            if not allowed or str(tool.name) in allowed
        ]
        await self._cache_write(cache_key, tools)
        return tools

    async def call_tool(
        self,
        endpoint: dict[str, Any],
        tool: str,
        arguments: dict[str, Any],
        *,
        deadline_seconds: float = 30.0,
    ) -> str:
        allowed = set(endpoint.get("allowed_tools") or [])
        if allowed and tool not in allowed:
            raise MCPClientError("TOOL_UNAVAILABLE", f"MCP tool '{tool}' is not allowed.")
        discovered = await self.list_tools(endpoint)
        if not any(item["tool"] == tool for item in discovered):
            raise MCPClientError("TOOL_UNAVAILABLE", f"MCP tool '{tool}' is not available.")

        async def invoke() -> str:
            transport, session = await self._with_session(endpoint)
            try:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
            except MCPClientError:
                raise
            except Exception as exc:
                raise MCPClientError("TOOL_UNAVAILABLE", "MCP tool call failed.") from exc
            finally:
                await self._close_session(transport, session)
            if getattr(result, "isError", False):
                raise MCPClientError("TOOL_UNAVAILABLE", "MCP server returned a tool error.")
            return "\n".join(
                str(block.text)
                for block in getattr(result, "content", [])
                if getattr(block, "type", None) == "text"
            )

        try:
            async with asyncio.timeout(deadline_seconds):
                return await invoke()
        except TimeoutError as exc:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP tool call timed out.") from exc


async def execute_calls(
    registry: MCPRegistry,
    calls: list[dict[str, Any]],
    endpoints: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute validated MCP calls concurrently and return safe result/error frames."""

    by_id = {
        MCPRegistry._endpoint_id(endpoint): endpoint
        for endpoint in endpoints
        if MCPRegistry._endpoint_id(endpoint)
    }

    async def execute(call: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        server_id, tool = str(call.get("server_id") or ""), str(call.get("tool") or "")
        endpoint = by_id.get(server_id)
        if endpoint is None or not tool:
            return None, {"code": "TOOL_UNAVAILABLE", "server": server_id, "tool": tool}
        try:
            content = await registry.call_tool(endpoint, tool, call.get("arguments") or {})
            return {
                "server_id": server_id,
                "tool": tool,
                "content": content,
                "source": endpoint.get("name") or server_id,
            }, None
        except MCPClientError as exc:
            return None, {"code": exc.code, "server": server_id, "tool": tool, "detail": exc.detail}

    outcomes = await asyncio.gather(*(execute(call) for call in calls))
    return [result for result, _ in outcomes if result], [error for _, error in outcomes if error]
