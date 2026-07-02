import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from langfuse import get_client
    from langfuse.langchain import CallbackHandler
    from opentelemetry import trace as _otel_trace
    _AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    get_client = None  # type: ignore[assignment]
    CallbackHandler = None  # type: ignore[assignment,misc]
    _otel_trace = None  # type: ignore[assignment]
    _AVAILABLE = False
    logger.warning("langfuse | import failed — Langfuse tracing disabled. Check langchain is installed.", exc_info=True)


def _configured() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(os.environ.get("LANGFUSE_SECRET_KEY"))


@lru_cache(maxsize=1)
def get_langfuse_client():
    """Singleton Langfuse client (SDK v3, OTEL-based). None nếu chưa cấu hình."""
    if not _AVAILABLE or not _configured():
        return None
    try:
        return get_client()
    except Exception:
        logger.exception("langfuse | failed to create client")
        return None


def get_langchain_callback_handler(conv_id: str | None = None):
    """CallbackHandler cho graph.astream_events của 1 agent run.

    2 nhánh hành vi tùy nguồn gốc request:
    - Có upstream OTEL context hợp lệ (request đến từ orchestrator, traceparent
      header đã được FastAPIInstrumentor — xem app.py — extract tự động thành
      current span context) → nối span vào ĐÚNG trace/parent đó, KHÔNG tạo
      trace riêng. Đây là trường hợp orchestrator gọi /api/conversations/run.
    - Không có upstream context (gọi thẳng workflow-runtime, vd. test 1 agent
      lẻ trong agent-builder, không qua orchestrator) → tạo trace riêng, trace_id
      ngẫu nhiên do SDK tự sinh (verify qua đọc source langfuse 3.15.0:
      `langfuse.langchain.CallbackHandler.__init__` chỉ nhận `public_key`,
      `update_trace`, `trace_context` — KHÔNG có `session_id`/`user_id`/
      `metadata` như v2). routers/traces.py's fallback theo sessionId=conv_id
      sẽ KHÔNG tra được trace của case này nữa — hạn chế đã biết, chưa xử lý.
    """
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
