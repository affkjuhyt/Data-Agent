"""Shared logging helpers.

`create_logger()` is kept for connector call-site compatibility. It deliberately
does not attach handlers or write files; records propagate to the process root
logger configured by `setup_logging()` or `libs.shared.observability`.
"""

import logging
import os
import sys

try:
    import ecs_logging

    _ECS_AVAILABLE = True
except ImportError:
    _ECS_AVAILABLE = False


def create_logger(logger_name: str, _file_name: str | None = None) -> logging.Logger:
    log = logging.getLogger(logger_name)
    log.setLevel(logging.INFO)
    return log


class _ServiceFilter(logging.Filter):
    """Attach service labels and OpenTelemetry ids to each record."""

    def __init__(self, service: str, env: str) -> None:
        super().__init__()
        self._service = service
        self._env = env

    def filter(self, record: logging.LogRecord) -> bool:
        # `labels` is a dynamic ECS field, not a declared LogRecord attribute —
        # read it into a typed local so the setdefault calls type-check.
        labels: dict[str, str] = getattr(record, "labels", {})
        labels.setdefault("service", self._service)
        labels.setdefault("env", self._env)
        record.labels = labels

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
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    handler.addFilter(_ServiceFilter(service_name, env))
    root.addHandler(handler)

    for lib in ("httpx", "httpcore", "uvicorn.access", "asyncio", "multipart"):
        logging.getLogger(lib).setLevel(logging.WARNING)
