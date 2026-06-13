"""
Snowflake connector for warehouse discovery and data retrieval.
Handles connection validation, schema discovery, and table queries.
"""
import asyncio
import logging
from typing import Any
import snowflake.connector
from snowflake.connector import DictCursor

log = logging.getLogger(__name__)


class SnowflakeConnector:
    """Manages Snowflake connections and metadata discovery."""

    def __init__(self):
        self.connection = None

    async def validate_and_connect(
        self,
        account_identifier: str,
        user: str,
        password: str,
        warehouse: str,
        database: str,
        schema: str,
        role: str = None,
    ) -> dict:
        """
        Test Snowflake connection and return success/error.

        Args:
            account_identifier: Snowflake account ID (e.g., 'abc12345.us-east-1')
            user: Snowflake username
            password: Snowflake password
            warehouse: Warehouse name
            database: Database name
            schema: Schema name
            role: Optional role to assume

        Returns:
            {
                "success": bool,
                "message": str,
                "tables": list[dict] if success else []
            }
        """
        def _connect():
            return snowflake.connector.connect(
                account=account_identifier,
                user=user,
                password=password,
                warehouse=warehouse,
                database=database,
                schema=schema,
                role=role,
                client_session_keep_alive=True,
            )

        try:
            log.info("Connecting to Snowflake account=%s database=%s", account_identifier, database)
            self.connection = await asyncio.to_thread(_connect)
            log.info("✓ Snowflake connection successful")

            tables = await self.discover_tables()

            return {
                "success": True,
                "message": f"Connected to {database}.{schema}",
                "tables": tables,
            }
        except Exception as e:
            log.error("✗ Snowflake connection failed: %s", e)
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
                "tables": [],
            }

    async def discover_tables(self) -> list[dict]:
        """Fetch list of tables in the current schema with row counts."""
        if not self.connection:
            return []

        def _query():
            cursor = self.connection.cursor(DictCursor)
            cursor.execute("""
                SELECT
                    TABLE_NAME,
                    TABLE_TYPE,
                    ROW_COUNT,
                    CREATED,
                    LAST_ALTERED
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = CURRENT_SCHEMA()
                  AND TABLE_TYPE IN ('BASE TABLE', 'DYNAMIC TABLE')
                ORDER BY TABLE_NAME
            """)
            return cursor.fetchall()

        try:
            rows = await asyncio.to_thread(_query)
            tables = [
                {
                    "table_id": row["TABLE_NAME"],
                    "table_name": row["TABLE_NAME"],
                    "table_type": row["TABLE_TYPE"],
                    "row_count": row["ROW_COUNT"] or 0,
                    "created_at": str(row["CREATED"]) if row["CREATED"] else None,
                    "last_altered": str(row["LAST_ALTERED"]) if row["LAST_ALTERED"] else None,
                }
                for row in rows
            ]
            log.info("Discovered %d tables", len(tables))
            return tables
        except Exception as e:
            log.error("Table discovery failed: %s", e)
            return []

    async def get_table_schema(self, table_name: str) -> dict:
        """Get column schema for a specific table."""
        if not self.connection:
            return {}

        def _query():
            cursor = self.connection.cursor(DictCursor)
            cursor.execute("""
                SELECT
                    COLUMN_NAME,
                    ORDINAL_POSITION,
                    DATA_TYPE,
                    IS_NULLABLE,
                    COLUMN_DEFAULT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = %s
                  AND TABLE_SCHEMA = CURRENT_SCHEMA()
                ORDER BY ORDINAL_POSITION
            """, (table_name,))
            return cursor.fetchall()

        try:
            rows = await asyncio.to_thread(_query)
            columns = [
                {
                    "name": row["COLUMN_NAME"],
                    "position": row["ORDINAL_POSITION"],
                    "type": row["DATA_TYPE"],
                    "nullable": row["IS_NULLABLE"] == "YES",
                    "default": row["COLUMN_DEFAULT"],
                }
                for row in rows
            ]
            return {"table_name": table_name, "columns": columns}
        except Exception as e:
            log.error("Schema fetch failed for table %s: %s", table_name, e)
            return {}

    async def query_table(
        self, table_name: str, limit: int = 100, offset: int = 0
    ) -> dict:
        """Query table data with pagination."""
        if not self.connection:
            return {"rows": [], "total_rows": 0}

        def _query():
            cursor = self.connection.cursor(DictCursor)
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {table_name}")  # noqa: S608 — table_name is server-controlled
            total = cursor.fetchone()["CNT"]
            cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit} OFFSET {offset}")  # noqa: S608
            return total, cursor.fetchall()

        try:
            total_rows, rows = await asyncio.to_thread(_query)
            return {
                "table_name": table_name,
                "rows": rows,
                "total_rows": total_rows,
                "limit": limit,
                "offset": offset,
            }
        except Exception as e:
            log.error("Query failed for table %s: %s", table_name, e)
            return {"rows": [], "total_rows": 0}

    def close(self) -> None:
        """Close Snowflake connection."""
        if self.connection:
            try:
                self.connection.close()
                self.connection = None
                log.info("Snowflake connection closed")
            except Exception as e:
                log.error("Error closing connection: %s", e)
