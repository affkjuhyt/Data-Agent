from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from langfuse import get_client
    from langfuse.langchain import CallbackHandler
    from opentelemetry import trace as _otel_trace
    _AVAILABLE = True
except ImportError:
    get_client = None  # type: ignore[assignment]
    CallbackHandler = None  # type: ignore[assignment,misc]
    _otel_trace = None  # type: ignore[assignment]
    _AVAILABLE = False
    # langfuse.langchain.CallbackHandler cần package "langchain" đầy đủ (không
    # chỉ langchain-core) — thiếu nó gây ImportError ngay từ import này, và
    # toàn bộ Langfuse tracing bị tắt âm thầm nếu không log rõ ở đây.
    logger.warning("langfuse | import failed — Langfuse tracing disabled. Check langchain is installed.", exc_info=True)


def _configured() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(os.environ.get("LANGFUSE_SECRET_KEY"))


@lru_cache(maxsize=1)
def get_langfuse_client():
    """Singleton Langfuse client (SDK v3, OTEL-based). None nếu chưa cấu hình
    (giữ tính chất 'Langfuse optional' — thiếu LANGFUSE_PUBLIC_KEY/SECRET_KEY
    thì toàn hệ thống chạy bình thường, chỉ không có trace)."""
    if not _AVAILABLE or not _configured():
        return None
    try:
        return get_client()
    except Exception:
        logger.exception("langfuse | failed to create client")
        return None


def get_langchain_callback_handler():
    """CallbackHandler gắn vào config['callbacks'] của graph.astream_events —
    tự tạo span cho từng node LangGraph. PHẢI gọi hàm này SAU KHI đã
    `with lf.start_as_current_span(...)` (xem routers/stream.py) — nếu gọi
    bare CallbackHandler() không có trace_context, nó không biết span cha là
    ai, tự coi mình là root (is_langchain_root=true) và đè lên trace đã mở,
    y hệt bug từng gặp ở workflow-runtime (xem observability.py bên đó)."""
    if not _AVAILABLE or not _configured():
        return None
    try:
        span_ctx = _otel_trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            trace_id = format(span_ctx.trace_id, "032x")
            parent_span_id = format(span_ctx.span_id, "016x")
            return CallbackHandler(trace_context={"trace_id": trace_id, "parent_span_id": parent_span_id})
        return CallbackHandler()
    except Exception:
        logger.exception("langfuse | failed to create callback handler")
        return None


def flush_langfuse() -> None:
    """Flush pending spans/generations to Langfuse. Gọi sau mỗi request."""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
        except Exception:
            logger.exception("langfuse | flush failed")
