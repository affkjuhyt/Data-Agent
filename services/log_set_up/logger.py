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
        return True


def setup_logging(service_name: str) -> None:
    env = os.environ.get("APP_ENV", "development")
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if _ECS_AVAILABLE:
        handler.setFormatter(ecs_logging.StderrLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))

    handler.addFilter(_ServiceFilter(service_name, env))
    root.addHandler(handler)

    # Giảm noise từ third-party libs
    for lib in ("httpx", "httpcore", "uvicorn.access", "asyncio", "multipart"):
        logging.getLogger(lib).setLevel(logging.WARNING)
