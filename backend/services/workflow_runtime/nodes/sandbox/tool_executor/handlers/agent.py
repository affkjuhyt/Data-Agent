from typing import Any
import logging
import uuid

from services.workflow_runtime.nodes.sandbox.tool_executor._utils import _get_query

logger = logging.getLogger(__name__)

_WORKFLOW_RUNTIME_URL = __import__("os").environ.get("WORKFLOW_RUNTIME_URL", "http://localhost:8001")


async def _delegate_to_agent_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    target_agent = call.get("arguments", {}).get("target_agent", "")
    prompt       = call.get("arguments", {}).get("prompt", "") or _get_query(call)

    if not target_agent or not prompt:
        return None, {"tool_name": "delegate_to_agent", "error": "target_agent and prompt are required"}

    runtime_url = _WORKFLOW_RUNTIME_URL
    tenant_id   = cfg.get("tenant_id", "")

    try:
        import httpx  # noqa: PLC0415
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{runtime_url}/api/conversations/run-sync",
                json={"agent_id": target_agent, "query": prompt},
                headers={"X-Tenant-ID": tenant_id} if tenant_id else {},
            )
            resp.raise_for_status()
            data = resp.json()
            result_text = data.get("response", str(data))
        logger.info("delegate_to_agent | target=%s → %d chars", target_agent, len(result_text))
        return {"id": f"delegate_{uuid.uuid4().hex[:8]}", "content": result_text,
                "source": f"tool/delegate/{target_agent}"}, None
    except Exception as e:
        return None, {"tool_name": "delegate_to_agent", "error": str(e)}
