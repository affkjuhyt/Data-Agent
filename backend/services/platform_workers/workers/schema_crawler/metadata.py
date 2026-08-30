"""Engine dispatch for schema discovery.

`KBWarehouse.service` is a free string set by whatever the connect UI sent
(`service=body.warehouse_type` in warehouse_connect.py), so it arrives spelled
however a user or a form chose — "Snowflake", "postgres", "PostgreSQL". Normalise
here, once, rather than letting every call site guess.

The crawl loop was hardcoded to Snowflake: `WHERE w.service = 'Snowflake'`, one
metadata module, one credential env var. That is why the Schema Catalog
described one set of databases while the DB Connector queried another.
"""
import logging
import os
from typing import Any

from services.platform_workers.workers.schema_crawler.identifiers import UnsafeIdentifier, safe_identifier

logger = logging.getLogger(__name__)

SNOWFLAKE = "snowflake"
POSTGRES = "postgres"
CLICKHOUSE = "clickhouse"

# Spellings seen in the wild, mapped to the engine that handles them. Unknown
# values are skipped with a warning rather than guessed at: crawling a warehouse
# with the wrong driver produces confident nonsense in the catalog, which is
# worse than an uncrawled warehouse.
_ALIASES: dict[str, str] = {
    "snowflake": SNOWFLAKE,
    "postgres": POSTGRES,
    "postgresql": POSTGRES,
    "postgre": POSTGRES,
    "pg": POSTGRES,
    "clickhouse": CLICKHOUSE,
    "click house": CLICKHOUSE,
}

SUPPORTED_ENGINES = (SNOWFLAKE, POSTGRES, CLICKHOUSE)


def normalize_engine(service: str | None) -> str | None:
    """Map a KBWarehouse.service value onto an engine, or None if unsupported."""
    return _ALIASES.get((service or "").strip().lower().replace("_", " "))


def discover_and_describe(
    engine: str, connection_params: dict[str, Any], selected_tables: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Crawl one warehouse. Output shape is identical across engines, so the
    caller's change detection, LLM description and Qdrant upsert stay generic.

    Imported lazily per engine: each pulls a different driver, and an image is
    not obliged to carry all three.
    """
    if engine == SNOWFLAKE:
        from services.platform_workers.workers.schema_crawler.snowflake_metadata import discover_and_describe as _run
    elif engine == POSTGRES:
        from services.platform_workers.workers.schema_crawler.postgres_metadata import discover_and_describe as _run
    elif engine == CLICKHOUSE:
        from services.platform_workers.workers.schema_crawler.clickhouse_metadata import discover_and_describe as _run
    else:
        raise ValueError(f"unsupported engine: {engine!r}")

    return _run(connection_params, selected_tables)


def table_watermark(
    engine: str, connection_params: dict[str, Any], schema: str, table: str, column: str,
) -> Any:
    """MAX(column) for one table, or None when it cannot be read.

    This is the only fact in the governance set that is measured rather than
    declared, and it is the one that lets an answer say "as of". Returns None —
    never a guess, never now() — on any failure: a watermark the crawler could
    not read must leave the table reported as UNKNOWN freshness, because the
    alternative is an "as of" timestamp that describes the crawl instead of the
    data.

    Never raises. One unreadable column (dropped, renamed, no grant) must not
    abort the crawl of a table whose schema is otherwise fine.
    """
    try:
        safe_identifier(table, "table")
        safe_identifier(column, "freshness column")
    except UnsafeIdentifier as e:
        logger.warning("table_watermark | %s, skipping freshness for %s.%s", e, schema, table)
        return None
    if schema:
        try:
            safe_identifier(schema, "schema")
        except UnsafeIdentifier as e:
            logger.warning("table_watermark | %s, skipping freshness for %s.%s", e, schema, table)
            return None

    if engine == SNOWFLAKE:
        from services.platform_workers.workers.schema_crawler.snowflake_metadata import max_timestamp as _run
    elif engine == POSTGRES:
        from services.platform_workers.workers.schema_crawler.postgres_metadata import max_timestamp as _run
    elif engine == CLICKHOUSE:
        from services.platform_workers.workers.schema_crawler.clickhouse_metadata import max_timestamp as _run
    else:
        logger.warning("table_watermark | unsupported engine %r", engine)
        return None

    try:
        return _run(connection_params, schema, table, column)
    except Exception as e:  # noqa: BLE001 — never raises, by contract (see docstring)
        logger.warning(
            "table_watermark | MAX(%s) on %s.%s failed (%s): freshness stays unknown",
            column, schema, table, e,
        )
        return None


def _password(engine: str, warehouse_id: str) -> str | None:
    """Crawler credential for one warehouse.

    Mirrors the Snowflake convention the crawl loop already used rather than inventing a
    scheme: a per-warehouse override, then a per-engine shared crawler account.
    KBWarehouseConfig.config still never persists a password (see
    select_warehouse_tables in warehouse_connect.py) — this remains the
    documented gap, now spelled the same way for every engine instead of only
    for Snowflake.

    Env names cannot contain "-", and warehouse_id is a UUID.
    """
    env_warehouse_id = warehouse_id.replace("-", "_")
    return (
        os.environ.get(f"WAREHOUSE_PASSWORD__{env_warehouse_id}")
        # Kept for compatibility with stacks configured before other engines
        # existed; SNOWFLAKE_* still works for Snowflake warehouses.
        or os.environ.get(f"SNOWFLAKE_PASSWORD__{env_warehouse_id}")
        or os.environ.get(f"{engine.upper()}_CRAWLER_PASSWORD")
        or os.environ.get("SNOWFLAKE_CRAWLER_PASSWORD" if engine == SNOWFLAKE else "")
        or None
    )


def _snowflake_params(conn: dict[str, Any], password: str) -> dict[str, Any] | None:
    if not conn.get("account"):
        return None
    return {
        "account": conn["account"],
        "user": conn.get("username", ""),
        "password": password,
        "warehouse": conn.get("warehouse"),
        "database": conn.get("database"),
        "schema": conn.get("schema"),
        "role": conn.get("role"),
    }


def _postgres_params(conn: dict[str, Any], password: str) -> dict[str, Any] | None:
    if not conn.get("host") or not conn.get("database"):
        return None
    return {
        "host": conn["host"],
        "port": conn.get("port") or 5432,
        "user": conn.get("username", "") or conn.get("user", ""),
        "password": password,
        "database": conn["database"],
        # Postgres groups tables under a schema within the database; the catalog
        # keys on it, so default rather than leave it blank.
        "schema": conn.get("schema") or "public",
    }


def _clickhouse_params(conn: dict[str, Any], password: str) -> dict[str, Any] | None:
    if not conn.get("host"):
        return None
    return {
        "host": conn["host"],
        "port": conn.get("port") or 8123,
        "user": conn.get("username", "") or conn.get("user", "") or "default",
        "password": password,
        "database": conn.get("database") or "default",
    }


def connection_params(
    engine: str, config: dict[str, Any], warehouse_id: str,
) -> dict[str, Any] | None:
    """Build driver connection params, or None when the warehouse is unusable.

    None covers a missing password and an incomplete stored config. Both are
    operator problems worth a log line and a skip, not a crash: one
    misconfigured warehouse must not stop every other tenant's catalog from
    being refreshed.
    """
    conn = (config or {}).get("connection") or {}
    password = _password(engine, warehouse_id)
    if not password:
        logger.warning(
            "connection_params | no password configured for %s warehouse=%s "
            "(set WAREHOUSE_PASSWORD__<id> or %s_CRAWLER_PASSWORD), skipping",
            engine, warehouse_id, engine.upper(),
        )
        return None

    builder = {
        SNOWFLAKE: _snowflake_params,
        POSTGRES: _postgres_params,
        CLICKHOUSE: _clickhouse_params,
    }.get(engine)
    if builder is None:
        return None

    params = builder(conn, password)
    if params is None:
        logger.warning(
            "connection_params | %s warehouse=%s has an incomplete stored connection, skipping",
            engine, warehouse_id,
        )
    return params
