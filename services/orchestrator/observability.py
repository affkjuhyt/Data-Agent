from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse
    _AVAILABLE = True
except ImportError:
    Langfuse = None  # type: ignore[assignment,misc]
    _AVAILABLE = False


@lru_cache(maxsize=1)
def get_langfuse_client():
    if not _AVAILABLE:
        return None
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key  = os.environ.get("LANGFUSE_SECRET_KEY")
    host        = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")
    if not public_key or not secret_key:
        return None
    try:
        return Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    except Exception:
        logger.exception("langfuse | failed to create client")
        return None
