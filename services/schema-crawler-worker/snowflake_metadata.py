"""Snowflake INFORMATION_SCHEMA crawl (doc §2.3).

Deliberately a thin, synchronous, standalone module (not the async
SnowflakeConnector in services/database_connector) — this worker is a
blocking cron/Kafka loop in the same style as memory-consumer/main.py, so a
sync call here costs nothing and keeps the worker self-contained the same
way memory-consumer doesn't import workflow-runtime's async Postgres client
either. discover_and_describe() is the one function tests monkeypatch to
avoid a real Snowflake account (see "Mock for now" in the implementation doc).
"""
import logging

import snowflake.connector
from snowflake.connector import DictCursor

logger = logging.getLogger(__name__)


def discover_and_describe(connection_params: dict, selected_tables: list[str] | None = None) -> dict[str, dict]:
    """Returns {table_name: {schema_name, table_name, columns, row_count, last_altered}}.

    connection_params: {account, user, password, warehouse, database, schema, role}
    selected_tables: restrict to these table names; None = every base/dynamic
    table in the configured schema (doc §2.3 — "Worker thu thập ... metadata
    cấp bảng").
    """
    conn = snowflake.connector.connect(
        account=connection_params["account"],
        user=connection_params["user"],
        password=connection_params["password"],
        warehouse=connection_params.get("warehouse"),
        database=connection_params.get("database"),
        schema=connection_params.get("schema"),
        role=connection_params.get("role"),
        client_session_keep_alive=False,
    )
    try:
        cursor = conn.cursor(DictCursor)
        cursor.execute("""
            SELECT TABLE_NAME, ROW_COUNT, LAST_ALTERED
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
              AND TABLE_TYPE IN ('BASE TABLE', 'DYNAMIC TABLE')
        """)
        table_stats = {row["TABLE_NAME"]: row for row in cursor.fetchall()}

        names = selected_tables or list(table_stats.keys())

        columns_by_table: dict[str, list[dict]] = {}
        if names:
            placeholders = ", ".join(["%s"] * len(names))
            cursor.execute(
                f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT, ORDINAL_POSITION
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME IN ({placeholders})
                    ORDER BY TABLE_NAME, ORDINAL_POSITION""",
                tuple(names),
            )
            for r in cursor.fetchall():
                columns_by_table.setdefault(r["TABLE_NAME"], []).append({
                    "name": r["COLUMN_NAME"],
                    "type": r["DATA_TYPE"],
                    "nullable": r["IS_NULLABLE"] == "YES",
                    "comment": r["COMMENT"],
                })

        result: dict[str, dict] = {}
        for table_name in names:
            columns = columns_by_table.get(table_name, [])
            if not columns:
                logger.warning("snowflake_metadata | table %s has no columns, skipping", table_name)
                continue
            stats = table_stats.get(table_name, {})
            result[table_name] = {
                "schema_name": connection_params.get("schema", ""),
                "table_name": table_name,
                "columns": columns,
                "row_count": stats.get("ROW_COUNT"),
                "last_altered": stats.get("LAST_ALTERED"),
            }
        return result
    finally:
        conn.close()
