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


@pytest.mark.req("DF-13")
def test_cursor_candidates_rank_a_modification_time_first_and_skip_what_cannot_grow() -> None:
    from libs.source_drivers.registry import cursor_candidates

    columns = [
        {
            "name": "id",
            "data_type": "int",
            "normalized_type": "integer",
            "is_key": True,
            "is_nullable": False,
        },
        {
            "name": "quantity",
            "data_type": "int",
            "normalized_type": "integer",
            "is_nullable": False,
        },
        {
            "name": "order_date",
            "data_type": "date",
            "normalized_type": "date",
            "is_nullable": False,
        },
        {
            "name": "created_at",
            "data_type": "timestamp",
            "normalized_type": "timestamp",
            "is_nullable": False,
        },
        {
            "name": "UpdatedAt",
            "data_type": "datetime2",
            "normalized_type": "timestamp",
            "is_nullable": True,
        },
        {"name": "note", "data_type": "text", "normalized_type": "string"},
        {"name": "amount", "data_type": "numeric", "normalized_type": "decimal"},
    ]
    got = cursor_candidates(columns)
    assert [c.name for c in got] == ["UpdatedAt", "created_at", "order_date", "id"]
    # A quantity is an integer that does not grow with the rows; a note or an amount never does.
    assert "rỗng" in got[0].hint and "rỗng" not in got[1].hint
    assert cursor_candidates(columns) == got


@pytest.mark.req("DF-13")
def test_a_due_or_end_date_ranks_last_and_a_birth_date_is_no_cursor() -> None:
    from libs.source_drivers.registry import cursor_candidates

    def ts(name: str, kind: str = "timestamp") -> dict[str, object]:
        return {"name": name, "data_type": kind, "normalized_type": kind, "is_nullable": False}

    got = cursor_candidates(
        [
            ts("due_date"),
            ts("ship_date"),
            ts("order_date"),
            ts("birth_date", "date"),
            ts("end_date"),
            ts("calendar_date", "date"),
            {
                "name": "customer_key",
                "data_type": "int",
                "normalized_type": "integer",
                "is_key": True,
                "is_nullable": False,
            },
        ]
    )
    # calendar_date is a date, not an end: "end" is a word of the name, not a part of a word.
    assert [c.name for c in got] == [
        "order_date",
        "ship_date",
        "calendar_date",
        "customer_key",
        "due_date",
        "end_date",
    ]
