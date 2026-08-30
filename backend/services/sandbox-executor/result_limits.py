"""How big a SQL result is allowed to get, in every dimension that can hurt.

`MAX_ROWS = 10_000` was the only bound, and a row count does not bound memory.
Ten thousand rows of a wide join — a few TEXT or JSONB columns, or one column
holding a document — is hundreds of megabytes, and every stage downstream keeps
its own copy: the driver materialises the result, the backend copies it into
dicts, the job store holds it for a 600s TTL (job_queue.py), the response
serialises it, and workflow-runtime folds it into agent state. A single
unbounded join could exhaust the executor without ever exceeding the row cap.

Four bounds, because they fail differently:

  * **rows** — the shape of the answer. Ten thousand rows is already past what
    any model can read; more is never useful.
  * **bytes** — the memory. This is the one that was missing.
  * **cell size** — one pathological value. A row cap and a byte cap together
    still let a single 200MB document through as "one small row".
  * **time** — a query that costs hours and returns three rows. No result-size
    bound sees it at all; only the engine's own timeout does.

Enforced twice, on purpose: server-side where the engine offers it (so the
database stops producing), and here while reading (so the executor stops
accumulating even when it does not).

Truncation is reported, never silent. A capped result that reaches the model
unlabelled becomes a confident total computed over an arbitrary prefix — worse
than an error, because nothing in the answer looks wrong.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("result_limits | %s=%r is not an integer, using %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("result_limits | %s=%d must be positive, using %d", name, value, default)
        return default
    return value


# Rows returned to the caller. One more than this is fetched so "there was more"
# can be distinguished from "that was all".
MAX_ROWS = _env_int("SQL_MAX_ROWS", 10_000)

# Total serialised size of the result. 8 MiB is far above any answerable
# question and far below what hurts the executor.
MAX_RESULT_BYTES = _env_int("SQL_MAX_RESULT_BYTES", 8 * 1024 * 1024)

# One value. Past this the content is not being read, it is being moved.
MAX_CELL_CHARS = _env_int("SQL_MAX_CELL_CHARS", 32 * 1024)

# Server-side wall clock for one query, seconds. Deliberately below
# workflow-runtime's own per-query budget (_QUERY_TIMEOUT_S plus
# POLL_OVERHEAD_S) so the database aborts and returns a real error rather than
# the caller giving up on a query that keeps running.
QUERY_TIMEOUT_S = _env_int("SQL_QUERY_TIMEOUT_S", 55)

# Rows pulled from the server per round-trip while streaming. Small enough that
# the byte cap can stop early on a wide result, large enough not to make a
# 10k-row read into 10k round-trips. It also sets how far the streaming backend
# can over-read past the cap: at most one batch.
FETCH_BATCH_ROWS = _env_int("SQL_FETCH_BATCH_ROWS", 500)

# Per-query memory ceiling on the ClickHouse SERVER (max_memory_usage), the one
# engine here that enforces one. This bounds the join itself, not its result —
# the case no client-side cap can reach, because by the time a row arrives the
# expensive part has already happened. 4 GiB is ClickHouse's own default; naming
# it here keeps it from silently becoming whatever a tenant's profile says.
CLICKHOUSE_MAX_MEMORY_BYTES = _env_int("SQL_CLICKHOUSE_MAX_MEMORY_BYTES", 4 * 1024 * 1024 * 1024)

# Reasons, closed set — these reach the answer, so they cannot be free text.
ROW_LIMIT = "row_limit"
BYTE_LIMIT = "byte_limit"

_CELL_SUFFIX = "…[truncated]"


@dataclass(frozen=True)
class CappedRows:
    rows: list[dict[str, Any]]
    is_truncated: bool = False
    reason: str = ""
    cells_truncated: int = 0
    result_bytes: int = 0

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def as_dict(self) -> dict[str, Any]:
        """The backend contract: what execute_readonly_query returns."""
        return {
            "rows": self.rows,
            "row_count": self.row_count,
            "is_truncated": self.is_truncated,
            "truncation_reason": self.reason,
            "cells_truncated": self.cells_truncated,
            "result_bytes": self.result_bytes,
        }


def _cap_cell(value: Any, max_cell_chars: int) -> tuple[Any, bool]:
    if isinstance(value, str) and len(value) > max_cell_chars:
        return value[:max_cell_chars] + _CELL_SUFFIX, True
    if isinstance(value, (bytes, bytearray)) and len(value) > max_cell_chars:
        return bytes(value[:max_cell_chars]), True
    return value, False


def _sizeof(row: dict[str, Any]) -> int:
    """Serialised size, measured the way the response will actually encode it.

    `default=str` matches the encoder used downstream, so a Decimal or a
    datetime is measured as the string it becomes rather than being guessed at.
    """
    try:
        return len(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        # Something json cannot describe even via str(). Charge it a nominal
        # size rather than failing the query over accounting.
        return len(repr(row))


@dataclass
class RowAccumulator:
    """Collects rows until one of the bounds is reached, then says stop.

    Incremental rather than a function over a finished list, because for the
    streaming backends the whole point is never to have the finished list: the
    caller stops fetching the moment `add` returns False.
    """

    max_rows: int = MAX_ROWS
    max_bytes: int = MAX_RESULT_BYTES
    max_cell_chars: int = MAX_CELL_CHARS

    rows: list[dict[str, Any]] = field(default_factory=list)
    total_bytes: int = 0
    cells_truncated: int = 0
    reason: str = ""

    def add(self, row: dict[str, Any]) -> bool:
        """Append `row`. False means this row was REJECTED — stop fetching.

        False is returned only on rejection, never merely because the budget is
        now full. The distinction matters: `reason` is set on exactly the paths
        that return False, so "we stopped early" and "the result is marked
        truncated" cannot come apart. An earlier version also returned False
        after admitting the row that reached the budget, which stopped the read
        with `reason` still empty and reported a silently-cut result as
        complete — the precise failure this module exists to prevent.

        The cost is over-reading by at most one batch, which the caller's own
        LIMIT already bounds.
        """
        if self.reason:
            return False
        if len(self.rows) >= self.max_rows:
            self.reason = ROW_LIMIT
            return False

        capped = {}
        for key, value in row.items():
            capped[key], was_cut = _cap_cell(value, self.max_cell_chars)
            if was_cut:
                self.cells_truncated += 1

        size = _sizeof(capped)
        # The first row is always kept, however large. Returning zero rows and a
        # byte-limit reason tells the caller nothing about what they asked for;
        # one over-budget row plus the flag does.
        if self.rows and self.total_bytes + size > self.max_bytes:
            self.reason = BYTE_LIMIT
            return False

        self.rows.append(capped)
        self.total_bytes += size
        return True

    def finish(self) -> CappedRows:
        if self.cells_truncated:
            logger.warning(
                "result_limits | %d cell(s) exceeded %d chars and were truncated",
                self.cells_truncated, self.max_cell_chars,
            )
        if self.reason:
            logger.warning(
                "result_limits | result truncated (%s) at %d row(s) / %d bytes",
                self.reason, len(self.rows), self.total_bytes,
            )
        return CappedRows(
            rows=self.rows,
            is_truncated=bool(self.reason),
            reason=self.reason,
            cells_truncated=self.cells_truncated,
            result_bytes=self.total_bytes,
        )


def cap_rows(
    rows: Iterable[dict[str, Any]],
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_RESULT_BYTES,
    max_cell_chars: int = MAX_CELL_CHARS,
) -> CappedRows:
    """Apply every bound to an already-materialised result.

    For backends whose driver has handed over the whole result before this code
    runs. It bounds what travels onward, not what the driver already allocated —
    only the streaming path (postgres_readonly) bounds peak memory.
    """
    accumulator = RowAccumulator(
        max_rows=max_rows, max_bytes=max_bytes, max_cell_chars=max_cell_chars,
    )
    for row in rows:
        if not accumulator.add(row):
            break
    return accumulator.finish()
