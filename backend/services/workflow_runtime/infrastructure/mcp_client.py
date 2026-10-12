"""MCP Streamable HTTP client used by the agent runtime.

The runtime only receives resolved bearer tokens from ``config_loader``. This module never puts
those tokens in a tool description, cache value, state channel, or log message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class MCPClientError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class MCPRegistry:
    """Discover and invoke one tenant's MCP endpoints over Streamable HTTP."""

    def __init__(self, redis_client: Any | None = None, cache_ttl: float = 60.0) -> None:
        if redis_client is not None:
            self._redis = redis_client
        elif os.getenv("REDIS_URL"):
            from redis.asyncio import from_url

            self._redis = from_url(os.environ["REDIS_URL"], decode_responses=True)
        else:
            self._redis = None
        self._cache_ttl = cache_ttl
        self._local_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    @staticmethod
    def _endpoint_id(endpoint: dict[str, Any]) -> str:
        return str(endpoint.get("server_id") or endpoint.get("id") or endpoint.get("name") or "")

    @staticmethod
    def _headers(endpoint: dict[str, Any]) -> dict[str, str]:
        token = str(endpoint.get("auth_token") or "")
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _cache_read(self, key: str) -> list[dict[str, Any]] | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
                return None
            except Exception:
                logger.warning("mcp | tool cache unavailable; using local cache", exc_info=True)
                self._redis = None
        cached = self._local_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        return None

    async def _cache_write(self, key: str, tools: list[dict[str, Any]]) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(key, json.dumps(tools, ensure_ascii=False), ex=int(self._cache_ttl))
                return
            except Exception:
                logger.warning("mcp | tool cache unavailable; using local cache", exc_info=True)
                self._redis = None
        self._local_cache[key] = (time.monotonic() + self._cache_ttl, tools)

    async def list_tools(self, endpoint: dict[str, Any]) -> list[dict[str, Any]]:
        endpoint_id = self._endpoint_id(endpoint)
        if not endpoint_id or not endpoint.get("url"):
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint is not configured.")
        cache_key = f"mcp:tools:{endpoint_id}"
        cached = await self._cache_read(cache_key)
        if cached is not None:
            return cached

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(
                str(endpoint["url"]), headers=self._headers(endpoint)
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    response = await session.list_tools()
        except Exception as exc:
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP tool discovery failed.") from exc

        tools = [
            {
                "server_id": endpoint_id,
                "server_name": str(endpoint.get("name") or endpoint_id),
                "tool": str(tool.name),
                "description": str(tool.description or ""),
                "input_schema": dict(tool.inputSchema or {}),
            }
            for tool in response.tools
            if not endpoint.get("allowed_tools")
            or str(tool.name) in set(endpoint["allowed_tools"])
        ]
        await self._cache_write(cache_key, tools)
        return tools

    async def call_tool(
        self,
        endpoint: dict[str, Any],
        tool: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> str:
        allowed = endpoint.get("allowed_tools") or []
        if allowed and tool not in allowed:
            raise MCPClientError("TOOL_UNAVAILABLE", f"MCP tool '{tool}' is not allowed.")
        if not endpoint.get("url"):
            raise MCPClientError("TOOL_UNAVAILABLE", "MCP endpoint is not configured.")
        discovered = await self.list_tools(endpoint)
        if not any(item.get("tool") == tool for item in discovered):
            raise MCPClientError("TOOL_UNAVAILABLE", f"MCP tool '{tool}' is not available.")

        async def _call() -> str:
            try:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamablehttp_client

                async with streamablehttp_client(
                    str(endpoint["url"]), headers=self._headers(endpoint)
                ) as (read_stream, write_stream, _session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, arguments)
            except MCPClientError:
                raise
            except Exception as exc:
                raise MCPClientError("TOOL_UNAVAILABLE", "MCP tool call failed.") from exc
            if getattr(result, "isError", False):
                raise MCPClientError("TOOL_UNAVAILABLE", "MCP server returned a tool error.")
            text_blocks = [
                str(block.text)
                for block in getattr(result, "content", [])
                if getattr(block, "type", None) == "text"
            ]
            return "\n".join(text_blocks)

        return await asyncio.wait_for(_call(), timeout=timeout)
