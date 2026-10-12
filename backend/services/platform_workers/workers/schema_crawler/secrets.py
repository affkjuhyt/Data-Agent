"""Synchronous OpenBao reader used by the schema crawler."""

def resolve_openbao_secret(path: str) -> str | None:
    """Resolve a path without importing optional hvac at module import time."""
    from services.platform_workers.workers.web_crawler.secrets import (
        resolve_openbao_secret as _resolve,
    )

    return _resolve(path)

__all__ = ["resolve_openbao_secret"]
