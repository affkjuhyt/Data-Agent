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
    session_id: str | None = None,
) -> tuple[str, str | None]:
    """
    Gọi POST /api/conversations/run trên workflow-runtime.
    Trả về (output_text, conversation_id).
    conversation_id dùng để frontend fetch LangFuse trace nội bộ của agent đó.
    """
    if not agent_id:
        raise ValueError("agent_id is empty — planner failed to assign an agent for this task.")

    payload = {"query": prompt, "agent_id": agent_id, "messages": []}
    if session_id:
        payload["conversation_id"] = session_id

    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"
    collected: list[str] = []
    conversation_id: str | None = None

    try:
        async with httpx.AsyncClient(timeout=config.AGENT_CALL_TIMEOUT) as client:
            async with client.stream("POST", url, json=payload,
                                     headers={"X-User-ID": user_id}) as resp:
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
