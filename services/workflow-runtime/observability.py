import os
import logging
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse
    from langfuse.callback import CallbackHandler
    from langfuse.client import StatefulSpanClient, StateType
    _LANGFUSE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    Langfuse = None  # type: ignore[assignment,misc]
    CallbackHandler = None
    StatefulSpanClient = None  # type: ignore[assignment,misc]
    StateType = None  # type: ignore[assignment]
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


@lru_cache(maxsize=1)
def _get_langfuse_raw_client():
    if not _LANGFUSE_AVAILABLE or Langfuse is None:
        return None
    cfg = _get_langfuse_config()
    if not cfg:
        return None
    try:
        return Langfuse(public_key=cfg["public_key"], secret_key=cfg["secret_key"], host=cfg["host"])
    except Exception:
        logger.exception("langfuse | failed to create raw client")
        return None


def flush_langfuse():
    """Flush pending spans to Langfuse. Call after each request."""
    lf = _get_langfuse_raw_client()
    if lf:
        try:
            lf.flush()
        except Exception:
            logger.exception("langfuse | flush failed")


def get_langfuse_handler(
    run_name:              str        = "agent-run",
    session_id:            str | None = None,
    user_id:               str | None = None,
    metadata:              dict | None = None,
    trace_id:              str | None = None,
    parent_observation_id: str | None = None,
):
    if not _LANGFUSE_AVAILABLE:
        return None

    cfg = _get_langfuse_config()
    if not cfg:
        return None

    # Orchestrated mode: attach agent spans directly under orchestrator's task span.
    # Dùng StatefulSpanClient để reference task span có sẵn, không tạo span mới.
    # Lưu ý: lf.client, lf.task_manager là internal API của langfuse>=2.0,<3.0 —
    # cần kiểm tra lại nếu nâng version.
    if trace_id and parent_observation_id:
        lf = _get_langfuse_raw_client()
        if lf and StatefulSpanClient is not None:
            try:
                task_span_ref = StatefulSpanClient(
                    client=lf.client,
                    id=parent_observation_id,
                    state_type=StateType.OBSERVATION,
                    trace_id=trace_id,
                    task_manager=lf.task_manager,
                    environment=getattr(lf, "_environment", None),
                )
                return CallbackHandler(stateful_client=task_span_ref, user_id=user_id)
            except Exception:
                logger.exception("langfuse | failed to create linked handler, falling back")

    # Standalone mode (single agent, no orchestrator context)
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
