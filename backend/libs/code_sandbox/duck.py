"""DuckDB for the analysis code, with its configuration locked (AL-05, ANALYTICS_SPEC §B.2).

Joins over several datasets, windows, pivots and percentiles are SQL's to write, and pandas' are
slow and easy to get wrong. The code gets one function, `sql("SELECT … FROM d1 JOIN d2 …")`, which
answers a DataFrame: its datasets are registered under their names, read in place. The code never
holds the connection (it lives in the function's closure, which the static check's ban on `__`
keeps out of reach) and cannot import duckdb.

What keeps DuckDB inside its box is its own configuration, set before the code runs and then
locked (`lock_configuration`): no external access (no file, no URL, no ATTACH of a file, no COPY
out), the local file system disabled, no extension installed or loaded, no temporary files. A SQL
naming one of those statements is also refused before DuckDB reads it (`forbidden`): two layers,
the configuration the boundary.

The settings live here, read by the runner of libs/code_sandbox/local.py (which passes them to the
child interpreter) and by `open_locked` for whoever opens a connection in process.
"""

from __future__ import annotations

import re
from typing import Any

# Small: the child runs under RLIMIT_AS (local.MEMORY_BYTES), pandas and the datasets included.
MEMORY_LIMIT = "256MB"
THREADS = 1

SETTINGS: tuple[str, ...] = (
    f"SET memory_limit='{MEMORY_LIMIT}'",
    f"SET threads={THREADS}",
    "SET max_temp_directory_size='0B'",
    "SET enable_external_access=false",
    "SET disabled_filesystems='LocalFileSystem'",
    "SET autoload_known_extensions=false",
    "SET autoinstall_known_extensions=false",
    "SET allow_community_extensions=false",
    "SET lock_configuration=true",
)

# Statements and functions that reach outside a query over registered tables. The configuration
# refuses them anyway; refusing the text first gives the model a reason it can act on.
FORBIDDEN_SQL = re.compile(
    r"\b(attach|detach|install|load|copy|export|import|pragma|call|checkpoint"
    r"|create\s+(or\s+replace\s+)?secret|read_\w+|glob|getenv|sniff_csv|parquet_\w+|duckdb_\w+)\b"
    # SET and RESET as statements, not a column called "set".
    r"|(^|;)\s*(set|reset)\b",
    re.IGNORECASE,
)


def forbidden(query: str) -> str | None:
    """The word that makes the SQL refused, None for a query the code may run."""
    found = FORBIDDEN_SQL.search(query)
    return found.group(0) if found else None


def open_locked(tables: dict[str, Any] | None = None) -> Any:
    """An in-memory connection with SETTINGS applied and locked, the tables registered."""
    import duckdb

    con = duckdb.connect(":memory:")
    for statement in SETTINGS:
        con.execute(statement)
    for name, frame in (tables or {}).items():
        con.register(name, frame)
    return con
