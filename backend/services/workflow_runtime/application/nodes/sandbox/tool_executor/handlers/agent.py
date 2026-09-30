"""delegate_to_agent — one agent asks another agent a question, in-process.

This used to POST to ``{WORKFLOW_RUNTIME_URL}/api/conversations/run-sync``,
an endpoint that never existed (every call 404'd). The orchestrator's agent
hop was merged into this process (see orchestrator/executor/agent_client.py),
so a delegation is now a direct call to ``agent_client.call_agent`` — same
process, no HTTP, no forged X-Tenant-ID header.

Identity: handlers receive the verified caller in ``cfg`` (tenant_id,
user_ref, user_roles — the same keys the audit trail reads, see
_utils.audit_ctx). Roles are passed through verbatim so the sub-agent's
kb_search/RBAC checks run as the REAL user, not as a service default.

``orchestrated=True`` tells the sub-agent it is running inside a larger turn,
so it skips per-turn extras such as follow-up suggestions (and cannot recurse
them).
"""
from typing import Any
import logging
import uuid

from services.workflow_runtime.application.nodes.sandbox.tool_executor._utils import _get_query

logger = logging.getLogger(__name__)


async def _delegate_to_agent_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    target_agent = call.get("arguments", {}).get("target_agent", "")
    prompt       = call.get("arguments", {}).get("prompt", "") or _get_query(call)

    if not target_agent or not prompt:
        return None, {"tool_name": "delegate_to_agent", "error": "target_agent and prompt are required"}

    try:
        # Lazy import: the orchestrator package pulls planner/observability at
        # import time, which must not be a cost of merely importing this
        # handler (tests load it standalone).
        from services.workflow_runtime.application.orchestrator.executor import agent_client  # noqa: PLC0415

        result_text, _conv_id, _citations = await agent_client.call_agent(
            target_agent,
            prompt,
            user_id=cfg.get("user_ref", "") or "",
            tenant_id=cfg.get("tenant_id", "") or "",
            user_roles=list(cfg.get("user_roles") or []),
            orchestrated=True,
        )
        logger.info("delegate_to_agent | target=%s → %d chars", target_agent, len(result_text))
        return {"id": f"delegate_{uuid.uuid4().hex[:8]}", "content": result_text,
                "source": f"tool/delegate/{target_agent}"}, None
    except Exception as e:
        return None, {"tool_name": "delegate_to_agent", "error": str(e)}
