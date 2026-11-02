"""Reading a customer's MySQL, SQL Server or ClickHouse, for every service that does (GEN-12).

worker-agent's executor and schema scan and worker-ingest's load reach the same engines with the
same connection rules: TLS from conn_options, the source's timeout, a read-only transaction where
the engine has one. They were in the executor only, so worker-ingest (another service, which
must not import worker-agent) could load nothing but PostgreSQL.

TLS fails closed, as the PostgreSQL path does (`ssl_mode`): a source without `sslmode` is
`verify-full`, and only an explicit `disable` (or the in-cluster demo service) sends anything in
clear. SQL Server's driver (FreeTDS) cannot check certificates, so a SQL Server source must say
`require` or `disable`; a verify mode is refused rather than silently downgraded. A ClickHouse
host is a host only: one that carries a path, userinfo or a query is refused
before a URL is built from it.

A driver returns (columns, rows): each column's name and the engine's type as text, each row's
values as the driver gives them (Decimal, date, bytes…); the caller turns them into what it
stores. A failure is a DriverError that names the engine and the error class, never the
credentials or the SQL. PostgreSQL stays with each caller's asyncpg, which they already hold.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import ssl
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, Protocol

Rows = tuple[list[dict[str, str]], list[list[Any]]]
# A batch of a streamed read: the columns (the same in every batch) and up to `batch` rows.
Batch = tuple[list[dict[str, str]], list[list[Any]]]


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
    options = getattr(source, "conn_options", None) or {}
    return options if isinstance(options, Mapping) else {}


SSL_MODES = ("disable", "require", "verify-ca", "verify-full")
_NOT_A_HOST = re.compile(r"[/?#@\\\s]")


def ssl_mode(source: Source) -> str:
    """The source's sslmode: as conn_options says, else verify-full. The local demo service
    (*.svc.cluster.local) has no CA bundle, so it is the one default that is `disable`."""
    host = getattr(source, "host", None) or ""
    default = "disable" if host.endswith(".svc.cluster.local") else "verify-full"
    mode = str(_options(source).get("sslmode") or default)
    if mode not in SSL_MODES:
        raise DriverError(f"sslmode {mode} is not one of {', '.join(SSL_MODES)}")
    return mode


def _columns(cursor: Any) -> list[dict[str, str]]:
    return [
        {"name": str(item[0]), "type": str(item[1] or "unknown")}
        for item in (cursor.description or ())
    ]


def _batches(cursor: Any, batch: int) -> Iterator[Batch]:
    """The cursor's rows `batch` at a time; one empty batch for an empty result, so the caller
    still learns the columns."""
    columns = _columns(cursor)
    sent = False
    while True:
        rows = cursor.fetchmany(batch)
        if not rows:
            break
        sent = True
        yield columns, [list(row) for row in rows]
    if not sent:
        yield columns, []


def _rows(cursor: Any) -> Rows:
    description = cursor.description or ()
    columns = [{"name": str(item[0]), "type": str(item[1] or "unknown")} for item in description]
    return columns, [list(row) for row in cursor.fetchall()]


def _mysql_tls(mode: str, ca: Any) -> dict[str, Any]:
    """PyMySQL's ssl options for `mode`. PyMySQL checks nothing unless it is given a CA (no
    certificate and no host name), so a verify mode without conn_options.ssl_ca uses the system's
    CA bundle, and is refused when the host has none. `require` encrypts without checking only
    when no CA is configured; a CA given is always checked."""
    cafile = str(ca) if ca else ssl.get_default_verify_paths().cafile
    if mode == "require" and not ca:
        return {"verify_mode": ssl.CERT_NONE, "check_hostname": False}
    if not cafile:
        raise DriverError("mysql TLS verification needs conn_options.ssl_ca on this host")
    return {
        "ca": cafile,
        "verify_mode": ssl.CERT_REQUIRED,
        "check_hostname": mode == "verify-full",
    }


def _mysql_connect(source: Source, user: str, password: str, timeout_s: int, **extra: Any) -> Any:
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
        **extra,
    }
    mode = ssl_mode(source)
    if mode != "disable":
        connect["ssl"] = _mysql_tls(mode, options.get("ssl_ca"))
    return pymysql.connect(**connect)


def mysql_batches(
    source: Source, user: str, password: str, sql: str, timeout_s: int, batch: int
) -> Iterator[Batch]:
    """`sql` read `batch` rows at a time through a server-side cursor (the client holds one batch,
    not the result), in a READ ONLY transaction rolled back at the end."""
    try:
        import pymysql.cursors

        conn = _mysql_connect(
            source, user, password, timeout_s, cursorclass=pymysql.cursors.SSCursor
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                options = _options(source)
                if options.get("schema"):
                    cursor.execute(f"USE `{str(options['schema']).replace('`', '``')}`")
                cursor.execute(sql)
                yield from _batches(cursor, batch)
            conn.rollback()
        finally:
            conn.close()
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"mysql query failed ({type(exc).__name__})") from exc


def mysql(source: Source, user: str, password: str, sql: str, timeout_s: int) -> Rows:
    """`sql` in a READ ONLY transaction (rolled back), in the source's schema when it names one."""
    try:
        options = _options(source)
        conn = _mysql_connect(source, user, password, timeout_s)
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
    mode = ssl_mode(source)
    if mode in ("verify-ca", "verify-full"):
        # FreeTDS encrypts but cannot be made to check the server's certificate from here: a
        # verify mode would be a silent downgrade, so it is refused and the source must say
        # `require` (encrypted, unverified) or `disable` in conn_options.
        raise DriverError(
            "sqlserver cannot verify the server certificate; set conn_options.sslmode to"
            " require or disable"
        )
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
            # FreeTDS encrypts the login and the session unless the source says disable.
            encryption="off" if mode == "disable" else "require",
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


def sqlserver_batches(
    source: Source, user: str, password: str, sql: str, timeout_s: int, batch: int
) -> Iterator[Batch]:
    """`sql` read `batch` rows at a time (FreeTDS streams the result set), in a transaction rolled
    back at the end."""
    mode = ssl_mode(source)
    if mode in ("verify-ca", "verify-full"):
        raise DriverError(
            "sqlserver cannot verify the server certificate; set conn_options.sslmode to"
            " require or disable"
        )
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
            appname="dataagent-ingest",
            tds_version=str(options.get("tds_version") or "7.4"),
            encryption="off" if mode == "disable" else "require",
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute(sql)
                yield from _batches(cursor, batch)
            conn.rollback()
        finally:
            conn.close()
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"sqlserver query failed ({type(exc).__name__})") from exc


def clickhouse_url(source: Source) -> str:
    """The source's HTTP endpoint: https unless its sslmode is disable; a host that would retarget
    the request (a slash, userinfo, a query, a space) is refused."""
    import httpx

    host = source.host or ""
    if not host or _NOT_A_HOST.search(host):
        raise DriverError("the source host is not a host name")
    scheme = "http" if ssl_mode(source) == "disable" else "https"
    return str(httpx.URL(scheme=scheme, host=host, port=int(source.port or 8123), path="/"))


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


async def _clickhouse_batches(
    source: Source, user: str, password: str, sql: str, timeout_s: int, batch: int
) -> AsyncIterator[Batch]:
    """`sql` over ClickHouse's HTTP interface, one JSON row per line, `batch` rows at a time."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout_s) as http:
        try:
            async with http.stream(
                "POST",
                clickhouse_url(source),
                params={
                    "database": source.database_name or "default",
                    "default_format": "JSONCompactEachRowWithNamesAndTypes",
                    "readonly": "1",
                },
                content=sql.encode(),
                auth=(user, password),
            ) as response:
                if response.status_code != 200:
                    raise DriverError(f"clickhouse query failed ({response.status_code})")
                lines = response.aiter_lines()
                names = json.loads(await anext(lines))
                types = json.loads(await anext(lines))
                columns = [
                    {"name": str(n), "type": str(t)} for n, t in zip(names, types, strict=True)
                ]
                rows: list[list[Any]] = []
                sent = False
                async for line in lines:
                    if not line.strip():
                        continue
                    rows.append(json.loads(line))
                    if len(rows) >= batch:
                        sent = True
                        yield columns, rows
                        rows = []
                if rows or not sent:
                    yield columns, rows
        except httpx.HTTPError as exc:
            raise DriverError(f"clickhouse query failed ({type(exc).__name__})") from exc
        except StopAsyncIteration as exc:
            raise DriverError("clickhouse query failed (no header)") from exc


async def _threaded(produce: Callable[[], Iterator[Batch]]) -> AsyncIterator[Batch]:
    """A blocking driver's batches, read in a thread and handed over through a queue of two:
    the driver waits while the caller writes, so at most a few batches are held at once."""
    handoff: queue.Queue[Any] = queue.Queue(maxsize=2)
    done = object()
    stop = threading.Event()

    def run() -> None:
        try:
            for item in produce():
                while not stop.is_set():
                    try:
                        handoff.put(item, timeout=1)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            handoff.put(done)
        except DriverError as exc:
            handoff.put(exc)
        except Exception as exc:  # noqa: BLE001 - any failure must reach the waiting caller
            handoff.put(DriverError(f"source read failed ({type(exc).__name__})"))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        while True:
            item = await asyncio.to_thread(handoff.get)
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()


async def stream(
    source: Source, user: str, password: str, sql: str, timeout_s: int, batch: int
) -> AsyncIterator[Batch]:
    """`sql` on a MySQL, SQL Server or ClickHouse source, `batch` rows at a time: a table larger
    than memory is read without holding it (RES-16). The columns come with every batch; an empty
    result is one empty batch."""
    if source.kind == "mysql":
        async for item in _threaded(
            lambda: mysql_batches(source, user, password, sql, timeout_s, batch)
        ):
            yield item
    elif source.kind == "sqlserver":
        async for item in _threaded(
            lambda: sqlserver_batches(source, user, password, sql, timeout_s, batch)
        ):
            yield item
    elif source.kind == "clickhouse":
        async for item in _clickhouse_batches(source, user, password, sql, timeout_s, batch):
            yield item
    else:
        raise DriverError(f"no driver for a {source.kind} source")
