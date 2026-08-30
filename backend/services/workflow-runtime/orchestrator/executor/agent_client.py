"""In-process agent calls from the orchestrator to workflow-runtime.

The orchestrator used to reach workflow-runtime over HTTP (`WORKFLOW_RUNTIME_URL`
+ `/api/conversations/run`) with a signed service assertion, a hand-built
`traceparent` header and a tenacity retry around the hop. It now lives inside
the same process, so an agent turn is a direct call to
`runtime_services.conversation_runner.run_conversation_stream(req, request, caller)`:

* `caller` is the same dict shape `libs.shared.auth.resolve_caller` produces
  (see `_caller_for`), so the runtime's tenant/role checks are unchanged —
  `user_roles` still travel verbatim and an empty list still fails closed in
  kb_search.
* the returned `StreamingResponse.body_iterator` is consumed and its SSE
  `data:` lines are parsed exactly as the HTTP body was before, so the event
  tuples yielded to graph.py / executor.py are unchanged.
* the Langfuse/OTEL context is ambient in-process, so no traceparent is needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from orchestrator import config

logger = logging.getLogger(__name__)

SERVICE_NAME = "orchestrator"


def _caller_for(user_id: str, tenant_id: str, user_roles: list[str] | None) -> dict[str, Any]:
    """The identity the orchestrator asserts on the REAL caller's behalf.

    Same keys `libs.shared.auth.make_get_current_user` hands every route, so
    `run_conversation_stream` cannot tell (and does not care) whether the turn
    came through Kong or from the orchestrator. Roles are passed through as
    given — never substituted — because workflow-runtime forwards them to
    kb-backend and an invented default would reopen the escalation this
    replaced.
    """
    return {
        "user_id":     user_id or "",
        "user_name":   user_id or "",
        "roles":       list(user_roles or []),
        "tenant_id":   tenant_id or "",
        "auth_source": f"service:{SERVICE_NAME}",
    }


class _InProcessRequest:
    """Minimal stand-in for the Starlette `Request` `run_conversation_stream`
    takes: it only reads `request.headers` (X-Channel) today. Kept tiny on
    purpose — anything else the runtime starts to need should be threaded
    through explicitly rather than faked here."""

    __slots__ = ("headers",)

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers: dict[str, str] = dict(headers or {})

    async def is_disconnected(self) -> bool:
        return False


async def _run_agent(payload: dict[str, Any], caller: dict[str, Any]) -> Any:
    """Start one agent turn in-process and return the runtime's response.

    Lazy import: `runtime_services.conversation_runner` pulls the whole graph
    stack (qdrant, mongo, ...) and must not be a cost of merely importing this
    module (tests load it standalone)."""
    from runtime_services.conversation_runner import run_conversation_stream
    from schemas import RunRequest

    req = RunRequest(**payload)
    # _InProcessRequest stands in for starlette's Request (only .headers is read).
    return await run_conversation_stream(req, _InProcessRequest(), caller)  # type: ignore[arg-type,unused-ignore]


async def _sse_lines(resp: Any) -> AsyncIterator[str]:
    """Yield the SSE lines of a StreamingResponse, honouring AGENT_CALL_TIMEOUT
    as an idle timeout between chunks (what the httpx read timeout enforced)."""
    status = getattr(resp, "status_code", 200)
    if status >= 400:
        body = getattr(resp, "body", b"")
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        raise RuntimeError(f"Agent HTTP {status}: {text}")

    iterator = resp.body_iterator.__aiter__()
    timeout = config.AGENT_CALL_TIMEOUT or None
    pending = ""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
            except StopAsyncIteration:
                break
            except TimeoutError as e:
                raise RuntimeError(f"Agent stream idle for more than {config.AGENT_CALL_TIMEOUT}s") from e
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            pending += chunk
            *lines, pending = pending.split("\n")
            for line in lines:
                yield line.rstrip("\r")
        if pending:
            yield pending.rstrip("\r")
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


async def call_agent(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    orchestrated: bool = False,
    guardrail_cfg: dict[str, Any] | None = None,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """
    Run one agent turn (workflow-runtime's POST /api/conversations/run, in-process).
    Trả về (output_text, conversation_id, citations).
    """
    if not agent_id:
        raise ValueError("agent_id is empty — planner failed to assign an agent for this task.")

    payload = {
        "query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": orchestrated,
        "guardrail_cfg": guardrail_cfg,
    }
    collected: list[str] = []
    citations: list[dict[str, Any]] = []

    resp = await _run_agent(payload, _caller_for(user_id, tenant_id, user_roles))
    conversation_id = resp.headers.get("X-Conversation-Id") or None
    async for line in _sse_lines(resp):
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
            elif isinstance(token, dict) and "__citations" in token:
                citations = token["__citations"]
        except json.JSONDecodeError:
            collected.append(raw)

    result = "".join(collected).strip()
    logger.debug("agent_call | agent=%s conv=%s len=%d", agent_id, conversation_id, len(result))
    return result, conversation_id, citations


_GUARDRAIL_CFG_KEYS = ("guardrail_model", "guardrail_max_tokens", "guardrail_timeout")


def extract_guardrail_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Chỉ lấy 3 field workflow-runtime cần để tự chạy output-guardrail check
    của chính nó — không gửi input_patterns/allowed_topics (không liên quan
    tới output check)."""
    return {k: cfg[k] for k in _GUARDRAIL_CFG_KEYS if k in cfg}


async def call_agent_stream(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    guardrail_cfg: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """
    Async generator — yield (event_type, data):
      ("conv_id", str)         — conversation id từ response header
      ("token", str)           — text token
      ("pending_review", dict) — tool approval interrupt
      ("chart", dict)          — chart_spec từ Visualization node (workflow-runtime)
      ("citations", list[dict]) — nguồn đã được responder trích dẫn (workflow-runtime)
      ("options", list[str])   — lựa chọn trả lời khi agent hỏi lại (responder <OPTIONS>)
      ("evidence", dict)       — SQL đã chạy, metric contract, provenance, rows preview
    """
    if not agent_id:
        raise ValueError("agent_id is empty")

    payload = {
        "query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": False,
        "guardrail_cfg": guardrail_cfg,
    }

    resp = await _run_agent(payload, _caller_for(user_id, tenant_id, user_roles))
    conv_id = resp.headers.get("X-Conversation-Id")
    if conv_id:
        yield ("conv_id", conv_id)
    async for line in _sse_lines(resp):
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
                pass  # orchestrator tự gen suggestions
            elif "__pending_review" in parsed:
                yield ("pending_review", parsed["__pending_review"])
            elif "__chart" in parsed:
                yield ("chart", parsed["__chart"])
            elif "__citations" in parsed:
                yield ("citations", parsed["__citations"])
            elif "__options" in parsed:
                # Clarification choices. Dropping these (as before) left the
                # user with a question and no way to answer it in one click.
                yield ("options", parsed["__options"])
            elif "__evidence" in parsed:
                yield ("evidence", parsed["__evidence"])
            # các event khác bỏ qua
