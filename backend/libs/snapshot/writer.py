"""A version's table → one Parquet file in da-snapshots (DF-04).

PostgreSQL sends the table as CSV (`COPY … TO STDOUT`) into a temporary file, which pyarrow parses
in C++ a block at a time with the column types the catalogue of PostgreSQL gives, and writes as
Parquet row groups: no row passes through a Python object, so two million rows fit a small pod.

The file is the same bytes for the same rows and provenance, so its sha256 names it: the rows come
in the table's physical order (`synchronize_seqscans` off, or a scan may start where another one
is), the encoder and its options are pinned, and the provenance is the caller's (org, dataset,
version, run, available_at). Every page carries its CRC (`write_page_checksum`), so a reader finds
a damaged page instead of reading wrong numbers.

A NUMERIC without a declared scale becomes decimal128(38, the largest scale in the column); one
whose values do not fit 38 digits, or hold NaN or ±Infinity, stays text, exact. A type Arrow has no
exact twin for (interval, json, uuid, money, …) is its text.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .store import SnapshotStore, StoreUnavailable

ENABLED = os.environ.get("SNAPSHOT_ENABLED", "1") != "0"
MAX_ROWS = int(os.environ.get("SNAPSHOT_MAX_ROWS", "20000000"))
# PoC-2 (model/eval/snapshot-poc2.md): what a write holds is the CSV parser's blocks and one row
# group. 2 053 532 AdventureWorks rows peak at 723 MiB with 16 MiB blocks, 1 048 576-row groups
# and a parser thread per core, at 196 MiB with these, for a file 8 % larger and as fast.
ROW_GROUP_ROWS = int(os.environ.get("SNAPSHOT_ROW_GROUP_ROWS", "131072"))
BLOCK_BYTES = 1 << 20
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class Snapshot:
    """A version written: `object_key` in `bucket`, `sha256` of the whole file."""

    object_key: str
    sha256: str
    row_count: int
    byte_size: int
    columns: list[dict[str, str]]
    created: bool  # False when the same file was in the bucket already
    status: str = "ok"


@dataclass(frozen=True)
class Skipped:
    """No file for this version, and why: too many rows, snapshots off, a value Arrow cannot
    hold, or the store refused. catalog.dataset_snapshot still gets a `skipped` row (DF-05)."""

    reason: str
    row_count: int | None = None
    status: str = "skipped"


@dataclass(frozen=True)
class _Column:
    name: str
    pg_type: str
    arrow: Any  # pyarrow.DataType
    select: str  # the expression COPY reads, already quoted
    tz: bool = False  # a timestamptz, sent as UTC wall time and typed UTC after parsing


def object_key(org_id: uuid.UUID, dataset_id: uuid.UUID, version: int, sha256: str) -> str:
    return f"{org_id}/{dataset_id}/v{version}/{sha256[:12]}.parquet"


async def write(
    conn: Any,
    *,
    schema: str,
    table: str,
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
    run_id: uuid.UUID | None,
    available_at: datetime.datetime,
    store: SnapshotStore,
    max_rows: int | None = None,
) -> Snapshot | Skipped:
    """Snapshot `schema.table` through `conn`, an asyncpg connection (under a SQLAlchemy session:
    its `get_raw_connection().driver_connection`), as version `version` of the dataset."""
    if not ENABLED:
        return Skipped("snapshots are off (SNAPSHOT_ENABLED=0)")
    if not (_NAME.match(schema) and _NAME.match(table)):
        raise ValueError(f"not a table name: {schema}.{table}")
    limit = MAX_ROWS if max_rows is None else max_rows
    with tempfile.TemporaryDirectory(prefix="snapshot-") as tmp:
        work = Path(tmp)
        if conn.is_in_transaction():
            result = await _write(
                conn,
                work,
                schema,
                table,
                limit,
                _provenance(org_id, dataset_id, version, run_id, available_at, schema, table),
            )
        else:
            async with conn.transaction(readonly=True):
                result = await _write(
                    conn,
                    work,
                    schema,
                    table,
                    limit,
                    _provenance(org_id, dataset_id, version, run_id, available_at, schema, table),
                )
        if isinstance(result, Skipped):
            return result
        path, sha, rows, columns = result
        key = object_key(org_id, dataset_id, version, sha)
        try:
            created = await store.put(key, path)
        except StoreUnavailable as exc:
            return Skipped(str(exc), rows)
        return Snapshot(key, sha, rows, path.stat().st_size, columns, created)


def _provenance(
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
    run_id: uuid.UUID | None,
    available_at: datetime.datetime,
    schema: str,
    table: str,
) -> dict[bytes, bytes]:
    return {
        b"dataagent.org_id": str(org_id).encode(),
        b"dataagent.dataset_id": str(dataset_id).encode(),
        b"dataagent.version": str(version).encode(),
        b"dataagent.run_id": str(run_id or "").encode(),
        b"dataagent.available_at": available_at.astimezone(datetime.UTC).isoformat().encode(),
        b"dataagent.source_table": f"{schema}.{table}".encode(),
    }


async def _write(
    conn: Any, work: Path, schema: str, table: str, limit: int, provenance: dict[bytes, bytes]
) -> tuple[Path, str, int, list[dict[str, str]]] | Skipped:
    # The physical order, and text that reads back the same in any session.
    await conn.execute(
        "SET LOCAL synchronize_seqscans = off; SET LOCAL TimeZone = 'UTC';"
        " SET LOCAL DateStyle = 'ISO, YMD'; SET LOCAL extra_float_digits = 3"
    )
    columns, rows = await _columns(conn, schema, table)
    if rows > limit:
        return Skipped(f"{rows} rows, over SNAPSHOT_MAX_ROWS ({limit})", rows)
    csv_path, parquet_path = work / "rows.csv", work / "version.parquet"
    select = ", ".join(c.select for c in columns)
    await conn.copy_from_query(
        f'SELECT {select} FROM "{schema}"."{table}"', output=str(csv_path), format="csv"
    )
    fields = [
        pa.field(c.name, pa.timestamp("us") if c.tz else c.arrow, metadata={"pg_type": c.pg_type})
        for c in columns
    ]
    final = pa.schema(
        [pa.field(c.name, c.arrow, metadata={"pg_type": c.pg_type}) for c in columns],
        metadata=provenance,
    )
    try:
        reader = pacsv.open_csv(
            csv_path,
            read_options=pacsv.ReadOptions(
                column_names=[c.name for c in columns], block_size=BLOCK_BYTES, use_threads=False
            ),
            parse_options=pacsv.ParseOptions(newlines_in_values=True),
            convert_options=pacsv.ConvertOptions(
                column_types={f.name: f.type for f in fields},
                # PostgreSQL writes NULL unquoted and an empty string as "".
                null_values=[""],
                strings_can_be_null=True,
                quoted_strings_can_be_null=False,
                true_values=["t"],
                false_values=["f"],
            ),
        )
        written = 0
        pending: list[Any] = []
        held = 0
        with pq.ParquetWriter(
            parquet_path,
            final,
            compression="zstd",
            write_page_checksum=True,
            write_statistics=True,
            version="2.6",
        ) as out:
            for batch in reader:
                pending.append(_typed(batch, final))
                held += batch.num_rows
                if held >= ROW_GROUP_ROWS:
                    out.write_table(pa.Table.from_batches(pending, final), ROW_GROUP_ROWS)
                    written += held
                    pending, held = [], 0
            if pending or not written:
                out.write_table(pa.Table.from_batches(pending, final), ROW_GROUP_ROWS)
                written += held
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        return Skipped(f"a value Parquet cannot hold: {exc}", rows)
    finally:
        csv_path.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with parquet_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    described = [
        {"name": c.name, "pg_type": c.pg_type, "arrow_type": str(c.arrow)} for c in columns
    ]
    return parquet_path, digest.hexdigest(), written, described


def _typed(batch: Any, schema: Any) -> Any:
    """A parsed batch in the file's types: a timestamptz's UTC wall time becomes UTC."""
    if batch.schema.equals(schema, check_metadata=False):
        return pa.RecordBatch.from_arrays(batch.columns, schema=schema)
    return pa.RecordBatch.from_arrays(
        [column.cast(field.type) for column, field in zip(batch.columns, schema, strict=True)],
        schema=schema,
    )


async def _columns(conn: Any, schema: str, table: str) -> tuple[list[_Column], int]:
    described = await conn.fetch(
        "SELECT a.attname, t.typname, format_type(a.atttypid, a.atttypmod) AS declared,"
        " CASE WHEN t.typname = 'numeric' AND a.atttypmod >= 4"
        "   THEN ((a.atttypmod - 4) >> 16) & 65535 END AS precision,"
        " CASE WHEN t.typname = 'numeric' AND a.atttypmod >= 4"
        "   THEN (a.atttypmod - 4) & 65535 END AS scale"
        " FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid"
        " WHERE a.attrelid = format('%I.%I', $1::text, $2::text)::regclass"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        schema,
        table,
    )
    if not described:
        raise ValueError(f"{schema}.{table} has no columns")
    # One scan: the rows, and what each NUMERIC without a scale holds.
    loose = [r["attname"] for r in described if r["typname"] == "numeric" and r["scale"] is None]
    measures = ["count(*)"]
    for name in loose:
        q = _quote(name)
        measures += [
            f"max(scale({q}))",
            f"max(length(trunc(abs({q}))::text)) FILTER"
            f" (WHERE {q} NOT IN ('NaN', 'Infinity', '-Infinity'))",
            f"count(*) FILTER (WHERE {q} IN ('NaN', 'Infinity', '-Infinity'))",
        ]
    stats = await conn.fetchrow(f'SELECT {", ".join(measures)} FROM "{schema}"."{table}"')
    rows = int(stats[0])
    loose_types: dict[str, Any] = {}
    for index, name in enumerate(loose):
        scale, digits, special = stats[1 + 3 * index : 4 + 3 * index]
        scale, digits = int(scale or 0), int(digits or 1)
        loose_types[name] = (
            pa.decimal128(38, scale) if not special and scale + digits <= 38 else pa.string()
        )
    columns: list[_Column] = []
    for r in described:
        name, kind, declared = str(r["attname"]), str(r["typname"]), str(r["declared"])
        q = _quote(name)
        if kind == "numeric":
            arrow = (
                loose_types[name]
                if r["scale"] is None
                else pa.decimal128(int(r["precision"]), int(r["scale"]))
                if int(r["precision"]) <= 38
                else pa.string()
            )
            columns.append(
                _Column(name, declared, arrow, q if arrow != pa.string() else f"{q}::text")
            )
        elif kind == "timestamptz":
            columns.append(
                _Column(
                    name,
                    declared,
                    pa.timestamp("us", tz="UTC"),
                    f"({q} AT TIME ZONE 'UTC')",
                    tz=True,
                )
            )
        elif kind in _EXACT:
            columns.append(_Column(name, declared, _EXACT[kind](), q))
        else:
            columns.append(_Column(name, declared, pa.string(), f"{q}::text"))
    return columns, rows


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


_EXACT: dict[str, Any] = {
    "int2": pa.int16,
    "int4": pa.int32,
    "int8": pa.int64,
    "float4": pa.float32,
    "float8": pa.float64,
    "bool": pa.bool_,
    "date": pa.date32,
    "timestamp": lambda: pa.timestamp("us"),
    "text": pa.string,
    "varchar": pa.string,
    "bpchar": pa.string,
    "name": pa.string,
}
