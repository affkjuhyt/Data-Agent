"""The database kinds the platform has a driver for, and what each can do (DF-12, F-24).

Adding an engine meant editing the api's connector cards, its list of queryable kinds, the
ingest api's list of loadable kinds, the ingest worker and the schema scan, each with its own
`if source.kind == …`. Each of those now asks here: a kind with no entry cannot be registered as a
source, is not offered in the connector picker, and is never dispatched to a code path that does
not exist (the cards offered Snowflake, which nothing could read).

`scan`, `profile` and `cursor` name how the workers read a kind's catalogue, profile its tables and
read a cursor's upper bound; the workers map each name to their implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Capabilities:
    federated: bool  # queried in place by the executor (libs.source_drivers.read)
    ingest: bool  # loaded by worker-ingest table jobs (libs.source_drivers.stream)
    dialect: str  # sqlglot's, as libs.sql_guard.dialect_of names it
    scan: str  # "pg_catalog" | "information_schema" | "clickhouse_system"
    profile: str  # "sql" | "clickhouse_http"
    cursor: str  # "pg_catalog" | "sql"
    stream: str  # "asyncpg" (read in a server-side cursor) | "driver" (libs.source_drivers.stream)
    quoted: str  # how a schema-qualified table is written, {schema} and {table}
    # DF-13: a table's columns and their types as the engine's catalogue names them, {schema} and
    # {table} plain identifiers; read before the rows, so a type does not come from the values.
    columns_sql: str


@dataclass(frozen=True)
class Driver:
    kind: str
    name: str
    category: Literal["database", "warehouse"]
    subtitle: str
    database_label: str
    default_port: int
    capabilities: Capabilities
    prep_notes: tuple[str, ...] = field(default_factory=tuple)
    sample_sql: str | None = None


class UnsupportedKind(ValueError):
    """No driver reads this kind of source."""


REGISTRY: dict[str, Driver] = {
    d.kind: d
    for d in (
        Driver(
            kind="postgresql",
            name="PostgreSQL",
            category="database",
            subtitle="Cơ sở dữ liệu quan hệ",
            database_label="Database",
            default_port=5432,
            capabilities=Capabilities(
                federated=True,
                ingest=True,
                dialect="postgres",
                scan="pg_catalog",
                profile="sql",
                cursor="pg_catalog",
                stream="asyncpg",
                quoted='"{schema}"."{table}"',
                columns_sql=(
                    "SELECT column_name, udt_name FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{table}' ORDER BY ordinal_position"
                ),
            ),
            prep_notes=("Tài khoản đọc chỉ cần quyền CONNECT và SELECT.",),
            sample_sql="GRANT CONNECT ON DATABASE <db> TO <user>;",
        ),
        Driver(
            kind="mysql",
            name="MySQL",
            category="database",
            subtitle="Cơ sở dữ liệu quan hệ",
            database_label="Database",
            default_port=3306,
            capabilities=Capabilities(
                federated=True,
                ingest=True,
                dialect="mysql",
                scan="information_schema",
                profile="sql",
                cursor="sql",
                stream="driver",
                quoted="`{schema}`.`{table}`",
                columns_sql=(
                    "SELECT COLUMN_NAME, COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' ORDER BY ORDINAL_POSITION"
                ),
            ),
            prep_notes=("Tài khoản chỉ có quyền SELECT trên database cần dùng.",),
            sample_sql="GRANT SELECT ON <database>.* TO '<user>'@'%';",
        ),
        Driver(
            kind="sqlserver",
            name="SQL Server",
            category="database",
            subtitle="Cơ sở dữ liệu quan hệ",
            database_label="Database",
            default_port=1433,
            capabilities=Capabilities(
                federated=True,
                ingest=True,
                dialect="tsql",
                scan="information_schema",
                profile="sql",
                cursor="sql",
                stream="driver",
                quoted="[{schema}].[{table}]",
                columns_sql=(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}' ORDER BY ORDINAL_POSITION"
                ),
            ),
            prep_notes=("Tài khoản thuộc role db_datareader của database cần dùng.",),
            sample_sql="ALTER ROLE db_datareader ADD MEMBER <user>;",
        ),
        Driver(
            kind="clickhouse",
            name="ClickHouse",
            category="warehouse",
            subtitle="Kho phân tích cột",
            database_label="Database",
            default_port=8123,
            capabilities=Capabilities(
                federated=True,
                ingest=True,
                dialect="clickhouse",
                scan="clickhouse_system",
                profile="clickhouse_http",
                cursor="sql",
                stream="driver",
                quoted="`{schema}`.`{table}`",
                columns_sql=(
                    "SELECT name, type FROM system.columns WHERE database = '{schema}' AND table = '{table}' ORDER BY position"
                ),
            ),
            prep_notes=("Tài khoản chỉ đọc trên database cần dùng.",),
            sample_sql="GRANT SELECT ON <database>.* TO <user>;",
        ),
    )
}


def driver(kind: str) -> Driver:
    found = REGISTRY.get(kind)
    if found is None:
        raise UnsupportedKind(f"no driver reads a {kind} source")
    return found


def federated_kinds() -> frozenset[str]:
    return frozenset(k for k, d in REGISTRY.items() if d.capabilities.federated)


def ingest_kinds() -> frozenset[str]:
    return frozenset(k for k, d in REGISTRY.items() if d.capabilities.ingest)


# DF-13: a source column's type, as its engine's catalogue names it, in the catalogue's vocabulary
# (catalog.column_meta.normalized_type), then the column type the workspace keeps it in. Types
# came from the first batch's values before: a timestamp or a JSON column landed as text.
NORMALIZED_TYPES = (
    "string",
    "integer",
    "decimal",
    "boolean",
    "date",
    "timestamp",
    "json",
    "binary",
)
WORKSPACE_TYPE = {
    "string": "text",
    # Whole numbers stay numeric: the tables loaded before keep the type they have.
    "integer": "numeric",
    "decimal": "numeric",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamptz",
    "json": "jsonb",
    "binary": "text",
}


def _unwrapped(source_type: str) -> str:
    """ClickHouse's Nullable(…) and LowCardinality(…), SQL Server's and MySQL's sizes, spaces."""
    value = source_type.strip().lower()
    for wrapper in ("nullable(", "lowcardinality("):
        while value.startswith(wrapper) and value.endswith(")"):
            value = value[len(wrapper) : -1].strip()
    return value


def type_map(kind: str, source_type: str) -> str:
    """The catalogue's type of a column `kind`'s engine calls `source_type`."""
    driver(kind)
    return normalized_type(source_type)


def normalized_type(source_type: str) -> str:
    """The catalogue's type of an engine's type name: PostgreSQL, MySQL, SQL Server and
    ClickHouse spell the same types apart (Float64, UInt32, Nullable(String), datetime2)."""
    value = _unwrapped(source_type)
    base = value.split("(")[0].strip()
    if value in ("bit", "tinyint(1)", "boolean", "bool") or base in ("bool", "boolean"):
        return "boolean"
    if base in ("json", "jsonb", "object") or base.startswith("json"):
        return "json"
    if base in (
        "bytea",
        "binary",
        "varbinary",
        "blob",
        "tinyblob",
        "mediumblob",
        "longblob",
        "image",
    ):
        return "binary"
    if base.startswith(("int", "uint", "bigint", "smallint", "tinyint", "mediumint")) or base in (
        "serial",
        "bigserial",
        "smallserial",
        "int2",
        "int4",
        "int8",
    ):
        return "integer"
    if base.startswith(
        ("decimal", "numeric", "float", "double", "real", "money", "smallmoney")
    ) or base in (
        "number",
        "float4",
        "float8",
    ):
        return "decimal"
    if base in ("date", "date32"):
        return "date"
    if base.startswith(("timestamp", "datetime", "smalldatetime")) or base in ("timestamptz",):
        return "timestamp"
    return "string"


def workspace_type(kind: str, source_type: str) -> str:
    """The workspace column type a `kind` source's `source_type` column lands in."""
    return WORKSPACE_TYPE[type_map(kind, source_type)]


# DF-13: the columns an incremental job can read past, best first. A cursor must only grow as
# rows are written or changed: a modification time catches updates, a creation or event time and
# an integer key only new rows. A date that says when something will end or fall due, or a birth
# date, does not grow with the rows at all. A nullable column loses the rows where it is empty,
# so it ranks after the same kind without nulls.
# Matched against the words of the name (snake_case and camelCase split), so `calendar_date` is
# not an end date and `UpdatedAt` is a modification time.
_MODIFIED = frozenset({"updated", "modified", "changed", "last", "ts", "timestamp", "sync"})
_CREATED = frozenset(
    {"created", "inserted", "order", "txn", "transaction", "event", "posted", "load", "loaded"}
)
_NOT_GROWING = frozenset({"due", "end", "expiry", "expires", "expiration", "until", "deadline"})
_NEVER = frozenset({"birth", "dob", "birthday"})
_KEY_LIKE = frozenset({"seq", "sequence", "version", "rowversion", "serial"})


def _words(name: str) -> frozenset[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return frozenset(w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w)


@dataclass(frozen=True)
class CursorCandidate:
    name: str
    data_type: str
    normalized_type: str
    hint: str


def cursor_candidates(columns: list[dict[str, object]]) -> list[CursorCandidate]:
    """The cursor candidates of a table's columns ({name, data_type, normalized_type,
    is_nullable, is_key}), ranked: a modification time, a creation or event time, another
    timestamp or date, an increasing integer key, then a date that does not grow with the rows
    (an end, a due date). Deterministic: the same columns give the same list."""
    ranked: list[tuple[int, int, str, CursorCandidate]] = []
    for column in columns:
        name = str(column.get("name") or "")
        kind = str(column.get("normalized_type") or "")
        lowered = name.lower()
        words = _words(name)
        if kind in ("timestamp", "date"):
            if words & _NEVER:
                continue
            day = kind == "date"
            if words & _MODIFIED:
                rank, hint = 0, "thời điểm sửa dòng: bắt được cả dòng mới lẫn dòng sửa"
            elif words & _NOT_GROWING or {"valid", "to"} <= words:
                rank, hint = 90, "ngày hạn hoặc ngày kết thúc: không tăng theo dòng mới, ít khi hợp"
            elif words & _CREATED or lowered == "date":
                rank, hint = (
                    10,
                    "thời điểm tạo hoặc phát sinh: chỉ bắt dòng mới, không bắt dòng sửa",
                )
            else:
                rank, hint = 20, "thời điểm: chỉ bắt dòng mới nếu không bao giờ sửa lùi"
            if day and rank < 90:
                rank += 1
                hint += "; theo ngày nên đọc lại cả ngày cuối (lookback 1 ngày)"
        elif kind == "integer" and (bool(column.get("is_key")) or bool(words & _KEY_LIKE)):
            rank, hint = 50, "khoá tăng dần: chỉ bắt dòng thêm mới, không bắt dòng sửa"
        else:
            continue
        nullable = bool(column.get("is_nullable", True))
        if nullable:
            hint += "; có thể rỗng, dòng rỗng sẽ không được nạp"
        ranked.append(
            (
                rank,
                int(nullable),
                lowered,
                CursorCandidate(name, str(column.get("data_type") or ""), kind, hint),
            )
        )
    return [c for *_, c in sorted(ranked, key=lambda r: (r[0], r[1], r[2]))]
