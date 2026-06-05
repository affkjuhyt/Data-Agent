import os
import logging
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from langfuse.callback import CallbackHandler
    _LANGFUSE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    CallbackHandler = None
    _LANGFUSE_AVAILABLE = False


class LangfuseHandlerParams(BaseModel):
    trace_name: str         = "agent-run"
    session_id: str | None  = None
    user_id:    str | None  = None
    metadata:   dict | None = None


def _valid_host(host: str) -> bool:
    parsed = urlparse(host)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@lru_cache(maxsize=1)
def _get_langfuse_config():
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host       = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

    if not public_key or not secret_key:
        return None

    if not _valid_host(host):
        logger.warning("langfuse | invalid LANGFUSE_HOST=%s", host)
        return None

    return {"public_key": public_key, "secret_key": secret_key, "host": host}


def get_langfuse_handler(
    run_name:   str        = "agent-run",
    session_id: str | None = None,
    user_id:    str | None = None,
    metadata:   dict | None = None,
):
    if not _LANGFUSE_AVAILABLE:
        return None

    cfg = _get_langfuse_config()
    if not cfg:
        return None

    params = LangfuseHandlerParams(
        trace_name=run_name,
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
    )

    try:
        return CallbackHandler(
            public_key=cfg["public_key"],
            secret_key=cfg["secret_key"],
            host=cfg["host"],
            trace_name=params.trace_name,
            session_id=params.session_id,
            user_id=params.user_id,
            metadata=params.metadata or {},
        )
    except Exception:
        logger.exception("langfuse | failed to create callback handler")
        return None
