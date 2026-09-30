from typing import Any
import asyncio
import logging
import uuid
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError

from services.workflow_runtime.domain.state import AgentState

logger = logging.getLogger(__name__)


class MCPResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source: str = ""


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


async def _execute_call(
    client: httpx.AsyncClient, call: dict[str, Any], mcp_endpoints: list[Any], fallback_query: str
) -> tuple[list[Any], dict[str, Any] | None]:
    tool_name = call.get("tool_name", "")
    endpoint = next(
        (ep for ep in mcp_endpoints if ep.get("name") == call.get("endpoint_name")),
        None,
    )
    if not endpoint:
        return [], {"tool_name": tool_name, "error": "no matching endpoint"}
    url = endpoint.get("url", "")
    if not _is_valid_url(url):
        return [], {"tool_name": tool_name, "endpoint": url, "error": "invalid url"}
    allowed_tools = endpoint.get("allowed_tools", [])
    if allowed_tools and tool_name not in allowed_tools:
        return [], {"tool_name": tool_name, "endpoint": endpoint.get("name"), "error": "tool not in whitelist"}
    headers = {}
    if token := endpoint.get("auth_token"):
        headers["Authorization"] = f"Bearer {token}"
    query = call.get("query") or fallback_query
    try:
        resp = await client.post(
            f"{url}/execute",
            json={"tool_name": tool_name, "query": query, **call.get("arguments", {})},
            headers=headers,
        )
        resp.raise_for_status()
        raw = resp.json().get("results", [])
        # Nhãn nguồn dùng cho citation = tên endpoint DO TENANT CẤU HÌNH (không phải
        # field server ngoài trả về) — MCP server có thể không gửi gì hoặc gửi tên
        # không đáng tin, nhưng endpoint.get("name") luôn an toàn để hiển thị vì
        # chính tenant đặt tên đó khi cấu hình.
        endpoint_label = endpoint.get("name") or f"mcp/{tool_name}"
        valid, skipped = [], 0
        for item in raw:
            try:
                result = MCPResult.model_validate(item).model_dump()
                result["source"] = endpoint_label
                valid.append(result)
            except ValidationError:
                skipped += 1
        if skipped:
            logger.warning("mcp | tool=%s — skipped %d invalid results", tool_name, skipped)
        logger.info("mcp | tool=%s endpoint=%s → %d results", tool_name, url, len(valid))
        return valid, None
    except Exception as e:
        logger.warning("mcp | tool=%s endpoint=%s failed: %s", tool_name, url, e)
        return [], {"tool_name": tool_name, "endpoint": url, "error": str(e)}


async def mcp_node(state: AgentState) -> dict[str, Any]:
    mcp_endpoints = state["config"].get("mcp_endpoints", [])
    if not mcp_endpoints:
        return {"mcp_results": [], "mcp_errors": []}

    mcp_calls = [tc for tc in state.get("tool_calls", []) if tc.get("type") == "mcp"]
    if not mcp_calls:
        return {"mcp_results": [], "mcp_errors": []}

    async with httpx.AsyncClient(timeout=10.0) as client:
        outcomes = await asyncio.gather(
            *[_execute_call(client, call, mcp_endpoints, state["query"]) for call in mcp_calls]
        )

    all_results = [item for results, _ in outcomes for item in results]
    all_errors = [err for _, err in outcomes if err]
    return {"mcp_results": all_results, "mcp_errors": all_errors}
