"""DF-12: every kind the driver registry offers is one the platform can read, written and
quoted the way the rest of the code expects."""

from __future__ import annotations

import pytest

from libs.source_drivers import registry
from libs.sql_guard import dialect_of

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("kind", sorted(registry.REGISTRY))
def test_a_registered_kind_is_read_quoted_and_scanned(kind: str) -> None:
    d = registry.driver(kind)
    caps = d.capabilities
    # The executor and the guard read it in the same dialect.
    assert dialect_of(kind) == caps.dialect
    assert "{schema}" in caps.quoted and "{table}" in caps.quoted
    assert caps.scan in {"pg_catalog", "information_schema", "clickhouse_system"}
    assert caps.profile in {"sql", "clickhouse_http"}
    assert caps.stream in {"asyncpg", "driver"} and caps.cursor in {"pg_catalog", "sql"}
    assert d.default_port > 0 and d.prep_notes


def test_a_kind_without_a_driver_is_refused() -> None:
    with pytest.raises(registry.UnsupportedKind):
        registry.driver("snowflake")
    assert registry.ingest_kinds() == {"postgresql", "mysql", "sqlserver", "clickhouse"}


@pytest.mark.parametrize(
    ("source_type", "normalized", "workspace"),
    [
        ("datetime", "timestamp", "timestamptz"),
        ("timestamp with time zone", "timestamp", "timestamptz"),
        ("Nullable(DateTime64(3))", "timestamp", "timestamptz"),
        ("json", "json", "jsonb"),
        ("jsonb", "json", "jsonb"),
        ("tinyint(1)", "boolean", "boolean"),
        ("bigint", "integer", "numeric"),
        ("Decimal(18, 2)", "decimal", "numeric"),
        ("date", "date", "date"),
        ("uniqueidentifier", "string", "text"),
        ("bytea", "binary", "text"),
    ],
)
def test_a_source_type_lands_in_its_workspace_type(
    source_type: str, normalized: str, workspace: str
) -> None:
    # DF-13: from the engine's catalogue, so a timestamp or a JSON column keeps its type.
    assert registry.normalized_type(source_type) == normalized
    assert registry.workspace_type("mysql", source_type) == workspace
    assert normalized in registry.NORMALIZED_TYPES
