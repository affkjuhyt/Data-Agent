"""A result set as Arrow IPC, the format datasets travel in to and from the python sandbox (AL-04).

The analysis tool sent every dataset to the sandbox as JSON rows (`{"columns", "rows"}`), the
whole of it on each call, every value spelled out (a date as "2013-01-01", a decimal as its digits,
a region's name once per row). Arrow keeps each column's type and stores it once: a column of
repeated names is dictionary-encoded, and the stream is compressed with zstd. On 10 000 rows of
dates, regions, products, amounts and counts the payload is 0.14 times the JSON.

Values whose column mixes types (an int beside a string) are sent as text, as JSON sent them; a
None stays a null. Decoding checks the stream before using it: the bytes may come from the pod,
where model-written code ran.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import io
from collections.abc import Sequence
from typing import Any

import pyarrow as pa

COMPRESSION = "zstd"


class IpcInvalid(ValueError):
    """The bytes are not one Arrow IPC stream within the limits."""


def _array(values: list[Any]) -> pa.Array:
    try:
        array = pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        array = pa.array([None if v is None else str(v) for v in values], type=pa.string())
    if pa.types.is_null(array.type):
        # A column of nulls only: typed as text, so a reader sees a column, not a null type.
        array = pa.array(values, type=pa.string())
    if pa.types.is_string(array.type) or pa.types.is_large_string(array.type):
        # One copy of each distinct value: a region's name repeated on every row is sent once.
        array = array.dictionary_encode()
    return array


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> pa.Table:
    arrays = [
        _array([row[i] if i < len(row) else None for row in rows]) for i in range(len(columns))
    ]
    return pa.Table.from_arrays(arrays, names=[str(c) for c in columns])


def encode(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """One Arrow IPC stream of the rows, zstd-compressed."""
    return encode_table(table(columns, rows))


def encode_table(data: pa.Table) -> bytes:
    sink = io.BytesIO()
    options = pa.ipc.IpcWriteOptions(compression=COMPRESSION)
    with pa.ipc.new_stream(sink, data.schema, options=options) as writer:
        writer.write_table(data)
    return sink.getvalue()


def sha256(data: bytes) -> str:
    """The stream's digest, what a compute run records of its input (ANALYTICS_SPEC §B.6)."""
    return hashlib.sha256(data).hexdigest()


def _plain(value: Any) -> Any:
    """A JSON-able scalar, as the JSON transport gave the code: a decimal as a float, a date as
    its ISO text."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def decode(
    data: bytes, *, max_rows: int | None = None, max_columns: int | None = None
) -> tuple[list[str], list[list[Any]]]:
    """The columns and rows of one stream, values as plain scalars. IpcInvalid on bytes that are
    not a stream, a stream that fails validation, or one past the limits."""
    try:
        read = pa.ipc.open_stream(pa.py_buffer(data)).read_all()
        read.validate(full=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OSError) as exc:
        raise IpcInvalid(f"not an Arrow IPC stream ({type(exc).__name__})") from exc
    if max_columns is not None and read.num_columns > max_columns:
        raise IpcInvalid(f"{read.num_columns} columns, more than {max_columns}")
    if max_rows is not None and read.num_rows > max_rows:
        raise IpcInvalid(f"{read.num_rows} rows, more than {max_rows}")
    columns = [str(n) for n in read.column_names]
    values = [read.column(i).to_pylist() for i in range(read.num_columns)]
    rows = [[_plain(col[r]) for col in values] for r in range(read.num_rows)]
    return columns, rows
