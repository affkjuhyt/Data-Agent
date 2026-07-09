from __future__ import annotations

import json
import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

import config

logger = logging.getLogger(__name__)

# Shared AsyncClient (connection pooling/keep-alive) — trước đây agent_client.py
# tạo mới 1 httpx.AsyncClient() cho MỖI lời gọi, không tái sử dụng connection.
_client: httpx.AsyncClient | None = None


def _traced_headers(base: dict, traceparent: str | None) -> dict:
    """Chèn header traceparent (W3C) dựng THỦ CÔNG từ (trace_id, span_id) của
    LangfuseSpan hiện tại — KHÔNG dựa vào opentelemetry.propagate.inject(),
    tường minh, không phụ thuộc ambient context."""
    headers = dict(base)
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def traceparent_of(span) -> str | None:
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
    orchestrated: bool = False,
    traceparent: str | None = None,
    guardrail_cfg: dict | None = None,
) -> tuple[str, str | None]:
    """
    Gọi POST /api/conversations/run trên workflow-runtime.
    Trả về (output_text, conversation_id).
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

    client = get_shared_client()
    headers = {"X-User-ID": user_id}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    headers = _traced_headers(headers, traceparent)

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
                except json.JSONDecodeError:
                    collected.append(raw)

    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Agent HTTP {e.response.status_code}: {e.response.text}") from e

    result = "".join(collected).strip()
    logger.debug("agent_call | agent=%s conv=%s len=%d", agent_id, conversation_id, len(result))
    return result, conversation_id


@_retry_transient
async def _open_agent_stream(client: httpx.AsyncClient, url: str, payload: dict, headers: dict):
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


def extract_guardrail_cfg(cfg: dict) -> dict:
    """Chỉ lấy 3 field workflow-runtime cần để tự chạy output-guardrail check
    của chính nó — không gửi input_patterns/allowed_topics (không liên quan
    tới output check)."""
    return {k: cfg[k] for k in _GUARDRAIL_CFG_KEYS if k in cfg}


async def call_agent_stream(
    agent_id: str,
    prompt: str,
    user_id: str = "orchestrator",
    tenant_id: str = "",
    traceparent: str | None = None,
    guardrail_cfg: dict | None = None,
):
    """
    Async generator — yield (event_type, data):
      ("conv_id", str)         — conversation id từ response header
      ("token", str)           — text token
      ("pending_review", dict) — tool approval interrupt
    """
    if not agent_id:
        raise ValueError("agent_id is empty")

    payload = {
        "query": prompt, "agent_id": agent_id, "messages": [], "orchestrated": False,
        "guardrail_cfg": guardrail_cfg,
    }
    url = f"{config.WORKFLOW_RUNTIME_URL}/api/conversations/run"

    client = get_shared_client()
    headers = {"X-User-ID": user_id}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    headers = _traced_headers(headers, traceparent)

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
                # bỏ qua __options và các event khác
    finally:
        await cm.__aexit__(None, None, None)
