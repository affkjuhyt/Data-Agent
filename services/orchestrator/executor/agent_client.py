from __future__ import annotations

import json
import logging

import httpx

import config

logger = logging.getLogger(__name__)


async def call_agent(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    orchestrated: bool = False,
    lf_trace_id: str | None = None,
    lf_parent_id: str | None = None,
) -> tuple[str, str | None]:
    """
    Gọi POST /api/conversations/run trên workflow-runtime.
    Trả về (output_text, conversation_id).
    lf_trace_id + lf_parent_id: truyền Langfuse context để workflow-runtime
    attach spans vào đúng task span của orchestrator.
    """
    if not agent_id:
        raise ValueError("agent_id is empty — planner failed to assign an agent for this task.")

    payload = {"query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": orchestrated}

    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"
    collected: list[str] = []
    conversation_id: str | None = None

    try:
        async with httpx.AsyncClient(timeout=config.AGENT_CALL_TIMEOUT) as client:
            headers = {"X-User-ID": user_id}
            if tenant_id:
                headers["X-Tenant-Id"] = tenant_id
            if lf_trace_id:
                headers["X-Langfuse-Trace-Id"] = lf_trace_id
            if lf_parent_id:
                headers["X-Langfuse-Parent-Id"] = lf_parent_id
            async with client.stream("POST", url, json=payload,
                                     headers=headers) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()
                conversation_id = resp.headers.get("X-Conversation-Id")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    if raw.startswith("[ERROR]"):
                        raise RuntimeError(f"Agent error: {raw}")
                    try:
                        token = json.loads(raw)
                        if isinstance(token, str):
                            collected.append(token)
                    except json.JSONDecodeError:
                        collected.append(raw)

    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Agent HTTP {e.response.status_code}: {e.response.text}") from e

    result = "".join(collected).strip()
    logger.debug("agent_call | agent=%s conv=%s len=%d", agent_id, conversation_id, len(result))
    return result, conversation_id


async def call_agent_stream(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    lf_trace_id: str | None = None,
    lf_parent_id: str | None = None,
):
    """
    Async generator — yield (event_type, data):
      ("conv_id", str)       — conversation id từ response header
      ("token", str)         — text token
      ("pending_review", dict) — tool approval interrupt
    Bỏ qua __suggestions từ workflow-runtime (orchestrator tự gen).
    lf_trace_id + lf_parent_id: truyền Langfuse context để workflow-runtime
    attach spans vào đúng span của orchestrator.
    """
    if not agent_id:
        raise ValueError("agent_id is empty")

    payload = {"query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": False}
    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"

    async with httpx.AsyncClient(timeout=config.AGENT_CALL_TIMEOUT) as client:
        headers = {"X-User-ID": user_id}
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        if lf_trace_id:
            headers["X-Langfuse-Trace-Id"] = lf_trace_id
        if lf_parent_id:
            headers["X-Langfuse-Parent-Id"] = lf_parent_id
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            conv_id = resp.headers.get("X-Conversation-Id")
            if conv_id:
                yield ("conv_id", conv_id)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw in ("[DONE]", ""):
                    break
                if raw.startswith("[ERROR]"):
                    raise RuntimeError(f"Agent error: {raw}")
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    yield ("token", raw)
                    continue
                if isinstance(parsed, str):
                    yield ("token", parsed)
                elif isinstance(parsed, dict):
                    if "__suggestions" in parsed:
                        pass  # bỏ qua, orchestrator tự gen
                    elif "__pending_review" in parsed:
                        yield ("pending_review", parsed["__pending_review"])
                    # bỏ qua __options và các event khác
