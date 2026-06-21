"""Named-logger factory for the database connectors.

Previously this attached its own file + stream handlers (writing to the repo
`logs/` dir) and set ``propagate = False`` — producing a plain log format that
diverged from the structured JSON the services emit via
``services/shared/observability.py``. It now just returns a standard named logger
that PROPAGATES to the root logger, so each process formats connector logs with
its own root config consistently:

* services (kb_backend, flow-builder, workflow-runtime, …) → JSON via
  ``setup_observability()``;
* the ingestion worker → its ``logging.basicConfig()``.

No own handlers, no file writes. ``file_name`` is kept for call-site
compatibility but is now ignored.
"""
import logging


def create_logger(logger_name: str, file_name: str | None = None) -> logging.Logger:
    log = logging.getLogger(logger_name)
    log.setLevel(logging.INFO)
    return log
import logging
import os
import sys

try:
    import ecs_logging
    _ECS_AVAILABLE = True
except ImportError:
    _ECS_AVAILABLE = False


class _ServiceFilter(logging.Filter):
    """Inject service name + env vào mọi log record để Loki có thể filter."""
    def __init__(self, service: str, env: str):
        super().__init__()
        self._service = service
        self._env = env

    def filter(self, record: logging.LogRecord) -> bool:
        record.labels = getattr(record, "labels", {})
        record.labels.setdefault("service", self._service)
        record.labels.setdefault("env", self._env)

        # opentelemetry-instrumentation-logging gắn otelTraceID/otelSpanID vào record
        # — đưa vào ECS field trace.id/span.id để nhảy Kibana <-> Jaeger
        trace_id = getattr(record, "otelTraceID", None)
        span_id = getattr(record, "otelSpanID", None)
        if trace_id and trace_id != "0":
            record.trace = {"id": trace_id}
        if span_id and span_id != "0":
            record.span = {"id": span_id}
        return True


def setup_logging(service_name: str) -> None:
    env = os.environ.get("APP_ENV", "development")
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if _ECS_AVAILABLE:
        handler.setFormatter(ecs_logging.StdlibFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))

    handler.addFilter(_ServiceFilter(service_name, env))
    root.addHandler(handler)

    # Giảm noise từ third-party libs
    for lib in ("httpx", "httpcore", "uvicorn.access", "asyncio", "multipart"):
        logging.getLogger(lib).setLevel(logging.WARNING)
