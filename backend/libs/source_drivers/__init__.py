"""Reading a customer's MySQL, SQL Server or ClickHouse, for every service that does (GEN-12).

worker-agent's executor and schema scan and worker-ingest's load reach the same engines with the
same connection rules: TLS from conn_options, the source's timeout, a read-only transaction where
the engine has one. They were in the executor only, so worker-ingest (another service, which
must not import worker-agent) could load nothing but PostgreSQL.

A driver returns (columns, rows): each column's name and the engine's type as text, each row's
values as the driver gives them (Decimal, date, bytes…); the caller turns them into what it
stores. A failure is a DriverError that names the engine and the error class, never the
credentials or the SQL. PostgreSQL stays with each caller's asyncpg, which they already hold.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Protocol

Rows = tuple[list[dict[str, str]], list[list[Any]]]


class DriverError(RuntimeError):
    """A source driver failed without exposing credentials or source SQL."""


class Source(Protocol):
    """What a driver reads of a catalog.Source."""

    kind: str
    host: str | None
    port: int | None
    database_name: str | None
    conn_options: Any


def _options(source: Source) -> Mapping[str, Any]:
    options = source.conn_options or {}
    return options if isinstance(options, Mapping) else {}


def _rows(cursor: Any) -> Rows:
    description = cursor.description or ()
    columns = [{"name": str(item[0]), "type": str(item[1] or "unknown")} for item in description]
    return columns, [list(row) for row in cursor.fetchall()]


def mysql(source: Source, user: str, password: str, sql: str, timeout_s: int) -> Rows:
    """`sql` in a READ ONLY transaction (rolled back), in the source's schema when it names one."""
    try:
        import pymysql

        options = _options(source)
        connect: dict[str, Any] = {
            "host": source.host,
            "port": source.port or 3306,
            "user": user,
            "password": password,
            "database": source.database_name,
            "connect_timeout": min(timeout_s, 30),
            "read_timeout": timeout_s,
            "write_timeout": timeout_s,
            "charset": str(options.get("charset") or "utf8mb4"),
        }
        if options.get("sslmode") not in (None, "disable"):
            ca = options.get("ssl_ca")
            if not ca:
                raise DriverError("mysql TLS requires conn_options.ssl_ca")
            connect["ssl"] = {"ca": str(ca), "check_hostname": options["sslmode"] == "verify-full"}
        conn = pymysql.connect(**connect)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                if options.get("schema"):
                    cursor.execute(f"USE `{str(options['schema']).replace('`', '``')}`")
                cursor.execute(sql)
                result = _rows(cursor)
            conn.rollback()
            return result
        finally:
            conn.close()
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"mysql query failed ({type(exc).__name__})") from exc


def sqlserver(
    source: Source, user: str, password: str, sql: str, timeout_s: int, appname: str = "dataagent"
) -> Rows:
    """`sql` in a transaction that is rolled back: T-SQL has no READ ONLY transaction, so the
    rollback undoes a write a guard missed, and the login is expected to be db_datareader."""
    try:
        import pymssql

        options = _options(source)
        conn = pymssql.connect(
            server=str(source.host),
            port=str(int(source.port or 1433)),
            user=user,
            password=password,
            database=source.database_name or "master",
            login_timeout=min(timeout_s, 30),
            timeout=timeout_s,
            appname=appname,
            tds_version=str(options.get("tds_version") or "7.4"),
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute(sql)
                result = _rows(cursor)
            conn.rollback()
            return result
        finally:
            conn.close()
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"sqlserver query failed ({type(exc).__name__})") from exc


def clickhouse_url(source: Source) -> str:
    options = _options(source)
    scheme = "https" if options.get("sslmode") not in (None, "disable") else "http"
    return f"{scheme}://{source.host}:{source.port or 8123}/"


async def clickhouse(
    source: Source,
    user: str,
    password: str,
    sql: str,
    timeout_s: int,
    *,
    client: Any = None,
    url: str | None = None,
    database: str | None = None,
) -> Rows:
    """`sql` over ClickHouse's HTTP interface with readonly=1, rows as JSON arrays."""
    import httpx

    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        response = await http.post(
            url or clickhouse_url(source),
            params={
                "database": database or source.database_name or "default",
                "default_format": "JSONCompact",
                "readonly": "1",
            },
            content=sql.encode(),
            auth=(user, password),
        )
    except httpx.HTTPError as exc:
        raise DriverError(f"clickhouse query failed ({type(exc).__name__})") from exc
    finally:
        if own:
            await http.aclose()
    if response.status_code != 200:
        raise DriverError(f"clickhouse query failed ({response.status_code})")
    body = json.loads(response.text)
    columns = [{"name": m["name"], "type": m["type"]} for m in body.get("meta", [])]
    return columns, [list(row) for row in body.get("data", [])]


async def read(source: Source, user: str, password: str, sql: str, timeout_s: int) -> Rows:
    """`sql` on a MySQL, SQL Server or ClickHouse source, off the event loop for the blocking
    drivers."""
    if source.kind == "mysql":
        return await asyncio.to_thread(mysql, source, user, password, sql, timeout_s)
    if source.kind == "sqlserver":
        return await asyncio.to_thread(sqlserver, source, user, password, sql, timeout_s)
    if source.kind == "clickhouse":
        return await clickhouse(source, user, password, sql, timeout_s)
    raise DriverError(f"no driver for a {source.kind} source")
