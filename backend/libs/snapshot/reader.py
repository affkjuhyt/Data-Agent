"""SQL on snapshots, with the reader's column policies (DF-04; DF-06 replays receipts through it).

A snapshot holds every column, as the version's table did: a policy applies when someone reads,
not when the file is written, so the file stays what the data was (DATA_PIPELINE_SPEC §1 B,
Security). The reader checks each file's sha256, then copies it into a temporary DuckDB database
as the reader may see it: a hidden column left out, a masked one replaced by its masking, the same
four methods as the query path (services/worker_agent/query/scope._masked). The SQL then runs on
that database opened read-only with external access off and the configuration locked, so it can
name the tables and nothing else: not the Parquet file (whose unmasked columns a `read_parquet`
would show), no other file, no extension, no network.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MASK_METHODS = frozenset({"hash", "partial", "last4", "null"})


class SnapshotMismatch(ValueError):
    """A file is not the snapshot the catalogue recorded: its sha256 differs."""


@dataclass(frozen=True)
class SnapshotFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class ReadResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    types: list[str] = field(default_factory=list)  # DuckDB's names: DECIMAL(38,2), VARCHAR, …


def query(
    tables: Mapping[str, SnapshotFile],
    sql: str,
    *,
    hidden: Mapping[str, Collection[str]] | None = None,
    masks: Mapping[str, Mapping[str, str]] | None = None,
    max_rows: int = 10_000,
    memory_limit: str = "512MB",
) -> ReadResult:
    """Run `sql` (DuckDB's dialect) on the snapshots, each readable under its key in `tables`;
    `hidden[table]` are the columns left out, `masks[table][column]` the method masking one."""
    hidden = hidden or {}
    masks = masks or {}
    with tempfile.TemporaryDirectory(prefix="snapshot-read-") as tmp:
        database = Path(tmp) / "snapshots.duckdb"
        build = duckdb.connect(str(database), config={"threads": 2, "memory_limit": memory_limit})
        try:
            for name, snapshot in tables.items():
                if not _NAME.match(name):
                    raise ValueError(f"not a table name: {name}")
                _verify(snapshot)
                columns = pq.read_schema(snapshot.path).names
                projection = _projection(
                    columns,
                    {c.lower() for c in hidden.get(name, ())},
                    {c.lower(): m for c, m in masks.get(name, {}).items()},
                    name,
                )
                path = str(snapshot.path).replace("'", "''")
                build.execute(
                    f"CREATE TABLE \"{name}\" AS SELECT {projection} FROM read_parquet('{path}')"
                )
        finally:
            build.close()
        read = duckdb.connect(
            str(database),
            read_only=True,
            config={
                "enable_external_access": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
                "threads": 2,
                "memory_limit": memory_limit,
                "lock_configuration": True,
            },
        )
        try:
            cursor = read.execute(sql)
            names = [d[0] for d in cursor.description or []]
            types = [str(d[1]) for d in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)
        finally:
            read.close()
    return ReadResult(names, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows, types)


def _verify(snapshot: SnapshotFile) -> None:
    digest = hashlib.sha256()
    with snapshot.path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != snapshot.sha256:
        raise SnapshotMismatch(f"{snapshot.path.name} is not the snapshot {snapshot.sha256[:12]}")


def _projection(columns: list[str], hidden: set[str], masks: dict[str, str], table: str) -> str:
    unknown = set(masks.values()) - MASK_METHODS
    if unknown:
        raise ValueError(f"unsupported mask method {sorted(unknown)[0]}")
    out = []
    for column in columns:
        key = column.lower()
        if key in hidden:
            continue
        q = '"' + column.replace('"', '""') + '"'
        method = masks.get(key)
        out.append(f"{_masked(q, method)} AS {q}" if method else q)
    if not out:
        raise ValueError(f"every column of {table} is hidden")
    return ", ".join(out)


def _masked(column: str, method: str) -> str:
    """As services/worker_agent/query/scope._masked writes it for PostgreSQL."""
    text = f"CAST({column} AS VARCHAR)"
    return {
        "hash": f"md5({text})",
        "partial": f"left({text}, 2) || '***'",
        "last4": f"'****' || right({text}, 4)",
        "null": "CAST(NULL AS VARCHAR)",
    }[method]
