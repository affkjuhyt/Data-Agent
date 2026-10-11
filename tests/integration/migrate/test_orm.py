"""The generated models (libs/dataagent_db) map exactly the tables, columns and keys of the DDL."""

import psycopg2
import pytest
from sqlalchemy.dialects import postgresql

from libs.dataagent_db import (
    Base,
    models,  # noqa: F401  (registers every table on Base.metadata)
)

pytestmark = pytest.mark.integration


def _live(dsn: str) -> dict[tuple[str, str], dict]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname, k.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                       NOT a.attnotnull,
                       coalesce(a.attnum = ANY (i.indkey), false)
                FROM pg_class k
                JOIN pg_namespace n ON n.oid = k.relnamespace
                JOIN pg_attribute a ON a.attrelid = k.oid AND a.attnum > 0 AND NOT a.attisdropped
                LEFT JOIN pg_index i ON i.indrelid = k.oid AND i.indisprimary
                WHERE k.relkind IN ('r', 'p') AND NOT k.relispartition
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
                """
            )
            out: dict = {}
            for schema, table, column, type_, nullable, in_pk in cur.fetchall():
                out.setdefault((schema, table), {})[column] = (type_, nullable, in_pk)
        return out
    finally:
        conn.close()


def test_every_table_is_mapped_column_for_column(ddl_server):
    live = _live(ddl_server["dataagent"]) | _live(ddl_server["dataagent_audit"])
    mapped = {(t.schema, t.name): t for t in Base.metadata.tables.values()}
    assert set(mapped) == set(live)
    dialect = postgresql.dialect()
    for key, table in mapped.items():
        columns = live[key]
        assert set(table.columns.keys()) == set(columns), key
        for col in table.columns:
            type_, nullable, in_pk = columns[col.name]
            compiled = col.type.compile(dialect=dialect).lower()
            # format_type() writes "numeric(3,2)", the compiler "NUMERIC(3, 2)".
            assert compiled.replace(" ", "") == type_.replace(" ", ""), (
                key,
                col.name,
                compiled,
                type_,
            )
            assert col.primary_key == in_pk, (key, col.name)
            assert col.nullable == nullable or col.primary_key, (key, col.name)
