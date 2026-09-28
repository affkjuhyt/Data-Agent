"""Shared observability bootstrap for every AeroFlow service.

One entry point — ``setup_observability(service_name, app=...)`` — gives every
service the SAME behaviour:

* **Structured JSON logs to stdout** via ``structlog``. Both ``structlog`` calls
  and plain ``logging.getLogger(...)`` calls are routed through the same JSON
  renderer, so a service can mix styles and still emit one consistent format.
* **Trace correlation**: every log line carries ``trace_id``/``span_id`` of the
  active OpenTelemetry span, so logs and traces line up in Jaeger/any backend.
* **Distributed tracing** via OpenTelemetry → OTLP/gRPC → Jaeger (endpoint from
  ``OTEL_EXPORTER_OTLP_ENDPOINT``). FastAPI + httpx + requests are auto-instrumented
  so the W3C ``traceparent`` header propagates across service hops automatically.

Everything degrades to a safe no-op: if the OTel libs are missing, the endpoint
is unset, or ``OTEL_SDK_DISABLED`` is truthy, the service still starts and still
logs JSON — it just does not export spans. Observability must never crash a service.

Import path differs per service build (same file, different sys.path root):
    from libs.shared.observability import setup_observability   # kb / flow_builder / workflow_runtime
    from shared.observability import setup_observability            # auth_api / minio / workers
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

import structlog

# OpenTelemetry is optional at import time — never let a missing dep crash a service.
# `trace` is annotated Any and pre-bound to None so the fallback assignment needs no
# `type: ignore` and type-checks the same whether or not the OTel libs are installed.
trace: Any = None
_OTEL = False
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    trace = _otel_trace
    _OTEL = True
except Exception:  # pragma: no cover - exercised only when libs are absent
    pass

_log = logging.getLogger("observability")


def _env_disabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes"}


# ── logging ────────────────────────────────────────────────────────────────
def _add_service_name(service_name: str) -> Callable[..., dict[str, Any]]:
    def processor(_logger: Any, _method_name: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict.setdefault("service", service_name)
        return event_dict

    return processor


def _add_trace_context(_logger: Any, _method_name: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject the active span's trace_id/span_id so logs correlate with traces."""
    if not _OTEL:
        return event_dict
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx is not None and ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(service_name: str, level: int | str = logging.INFO) -> None:
    """JSON logs to stdout for structlog AND stdlib logging, with trace context."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_service_name(service_name),
        _add_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route plain `logging` records through the same JSON renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


# ── tracing ──────────────────────────────────────────────────────────────────
def init_tracing(service_name: str) -> bool:
    """Set up an OTLP→gRPC TracerProvider once. Returns True if tracing is live."""
    if not _OTEL or _env_disabled():
        return False

    # Idempotent: skip if a real SDK provider is already installed.
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return True

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    try:
        resource = Resource.create(
            {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name)}
        )
        provider = TracerProvider(resource=resource)
        # gRPC OTLP on :4317 is plaintext in our stack → insecure=True.
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://"))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return True
    except Exception:
        _log.warning("observability | tracing init failed; continuing without spans", exc_info=True)
        return False


def _instrument_logging() -> None:
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception:
        pass


def instrument_fastapi(app: Any) -> None:
    """Auto-instrument a FastAPI app + its outbound httpx/requests calls."""
    if not _OTEL or _env_disabled():
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        _log.warning("observability | FastAPI instrumentation skipped", exc_info=True)
    _instrument_http_clients()
    _instrument_logging()


def _instrument_http_clients() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
    except Exception:
        pass


def _instrument_databases() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

        PymongoInstrumentor().instrument()
    except Exception:
        pass


def setup_observability(service_name: str, app: Any = None) -> None:
    """Single call for any service.

    FastAPI services pass ``app`` to get request/response spans + header
    propagation. Worker loops omit it and get logging + tracing + instrumented
    outbound HTTP clients (httpx/requests) for cross-service correlation.
    """
    configure_logging(service_name)
    init_tracing(service_name)
    _instrument_databases()
    if app is not None:
        instrument_fastapi(app)
    else:
        _instrument_http_clients()
        _instrument_logging()


def get_tracer(name: str) -> Any:
    """Convenience accessor for manual spans (no-op tracer if OTel is absent)."""
    if not _OTEL:
        return None
    return trace.get_tracer(name)
