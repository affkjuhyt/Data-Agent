from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import json
import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

import config
from libs.shared.service_identity import get_secret, mint_service_assertion

logger = logging.getLogger(__name__)

# Shared AsyncClient (connection pooling/keep-alive) — trước đây agent_client.py
# tạo mới 1 httpx.AsyncClient() cho MỖI lời gọi, không tái sử dụng connection.
_client: httpx.AsyncClient | None = None


SERVICE_NAME = "orchestrator"


def _identity_headers(user_id: str, tenant_id: str, user_roles: list[str] | None) -> dict[str, Any]:
    """Assert the REAL caller's identity to workflow-runtime.

    This call used to send a bare `X-User-ID` (plus an optional tenant) and no
    roles at all, which workflow-runtime accepted without checking anything.
    Two consequences, both fixed here: anyone reaching port 8001 could claim to
    be the orchestrator, and because no roles arrived, the downstream agent had
    nothing to act on — which is what drove kb_search to hardcode an admin set.

    Signed with the same shared secret as every other service hop
    (backend/libs/shared/service_identity.py), so workflow-runtime takes identity from
    the token and ignores headers entirely.
    """
    return {
        "X-Service-Assertion": mint_service_assertion(
            service=SERVICE_NAME,
            tenant_id=tenant_id,
            user_id=user_id,
            roles=user_roles or [],
            secret=get_secret(),
        )
    }


def _traced_headers(base: dict[str, Any], traceparent: str | None) -> dict[str, Any]:
    """Chèn header traceparent (W3C) dựng THỦ CÔNG từ (trace_id, span_id) của
    LangfuseSpan hiện tại — KHÔNG dựa vào opentelemetry.propagate.inject(),
    tường minh, không phụ thuộc ambient context."""
    headers = dict(base)
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def traceparent_of(span: Any) -> str | None:
    """Dựng chuỗi traceparent W3C từ 1 LangfuseSpan (span.trace_id, span.id)."""
    if span is None:
        return None
    trace_id = getattr(span, "trace_id", None)
    span_id = getattr(span, "id", None)
    if not trace_id or not span_id:
        return None
    return f"00-{trace_id}-{span_id}-01"


def get_shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=config.AGENT_CALL_TIMEOUT, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _client


async def close_shared_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        return True
    return False


_retry_transient = retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=2),
    reraise=True,
)


@_retry_transient
async def call_agent(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    orchestrated: bool = False,
    traceparent: str | None = None,
    guardrail_cfg: dict[str, Any] | None = None,
) -> tuple[str, str | None, list[dict]]:
    """
    Gọi POST /api/conversations/run trên workflow-runtime.
    Trả về (output_text, conversation_id, citations).
    """
    if not agent_id:
        raise ValueError("agent_id is empty — planner failed to assign an agent for this task.")

    payload = {
        "query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": orchestrated,
        "guardrail_cfg": guardrail_cfg,
    }
    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"
    collected: list[str] = []
    conversation_id: str | None = None
    citations: list[dict] = []

    client = get_shared_client()
    headers = _traced_headers(_identity_headers(user_id, tenant_id, user_roles), traceparent)

    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
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
                    elif isinstance(token, dict) and "__citations" in token:
                        citations = token["__citations"]
                except json.JSONDecodeError:
                    collected.append(raw)

    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Agent HTTP {e.response.status_code}: {e.response.text}") from e

    result = "".join(collected).strip()
    logger.debug("agent_call | agent=%s conv=%s len=%d", agent_id, conversation_id, len(result))
    return result, conversation_id, citations


@_retry_transient
async def _open_agent_stream(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any], headers: dict[str, Any]
) -> tuple[Any, httpx.Response]:
    """Mở connection SSE — retry ở đây (trước khi đọc byte nào) là an toàn.
    KHÔNG retry phần đọc aiter_lines() bên dưới (call_agent_stream) — 1
    generator đã yield token rồi mà retry lại sẽ gây duplicate token cho
    client, nên phần đó để lỗi propagate thẳng lên nếu đứt giữa chừng."""
    cm = client.stream("POST", url, json=payload, headers=headers)
    resp = await cm.__aenter__()
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        await cm.__aexit__(None, None, None)
        raise
    return cm, resp


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
    traceparent: str | None = None,
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
    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"

    client = get_shared_client()
    headers = _traced_headers(_identity_headers(user_id, tenant_id, user_roles), traceparent)

    cm, resp = await _open_agent_stream(client, url, payload, headers)
    try:
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
    finally:
        await cm.__aexit__(None, None, None)
