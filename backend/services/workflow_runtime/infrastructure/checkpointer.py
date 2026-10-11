"""The shared durable LangGraph checkpointer for workflow_runtime.

Both the single-agent and orchestrator graphs use the same Postgres pool.  Keeping
pool creation here prevents one service replica from opening two independent saver
implementations with subtly different serialization or search-path settings.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_ASYNC_DRIVER_RE = re.compile(r"^postgresql\+\w+://")
_CHECKPOINTER_SCHEMA = "langgraph"
_SERDE: Any = None

_pool: Any = None
_saver: Any = None
_lock = asyncio.Lock()


def normalize_checkpointer_dsn(url: str) -> str:
    if not url:
        raise ValueError("RUNTIME_DB_URL is required for the Postgres checkpointer")
    return _ASYNC_DRIVER_RE.sub("postgresql://", url)


async def get_checkpointer() -> AsyncPostgresSaver:
    """Open and initialise the process-shared Postgres saver once."""
    global _pool, _saver, _SERDE
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from psycopg_pool import AsyncConnectionPool

    if _SERDE is None:
        _SERDE = JsonPlusSerializer(pickle_fallback=True)
    if _saver is not None:
        return _saver
    async with _lock:
        if _saver is not None:
            return _saver
        dsn = normalize_checkpointer_dsn(os.environ.get("RUNTIME_DB_URL", ""))
        _pool = AsyncConnectionPool(
            conninfo=dsn,
            max_size=10,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": None,
                "options": f"-c search_path={_CHECKPOINTER_SCHEMA}",
            },
        )
        await _pool.open()
        _saver = AsyncPostgresSaver(_pool, serde=_SERDE)
        await _saver.setup()
        return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
    _pool = None
    _saver = None
