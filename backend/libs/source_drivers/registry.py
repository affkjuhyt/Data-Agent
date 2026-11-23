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
