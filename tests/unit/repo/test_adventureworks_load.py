"""`make adventureworks` (tests/e2e/adventureworks/load.py): the SQL Server install script becomes
PostgreSQL that loads the way BULK INSERT did, and every file it reads is pinned."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

pytestmark = [pytest.mark.unit]

HERE = pathlib.Path(__file__).parents[2] / "e2e" / "adventureworks"
_spec = importlib.util.spec_from_file_location("aw_load", HERE / "load.py")
assert _spec is not None and _spec.loader is not None
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

SCRIPT = """
CREATE TABLE [dbo].[DimThing](
	[ThingKey] [int] IDENTITY(1,1) NOT NULL,
	[ThingAlternateKey] [nvarchar](25) NULL,
	[SpanishThingName] [nvarchar](50) NOT NULL,
	[ListPrice] [money] NULL,
	[IsCurrent] [bit] NOT NULL,
	[LargePhoto] [varbinary](max) NULL
) ON [PRIMARY];
GO
CREATE TABLE [dbo].[FactThingSales](
	[ThingKey] [int] NOT NULL,
	[SalesAmount] [money] NOT NULL
) ON [PRIMARY];
GO
CREATE TABLE [dbo].[sysdiagrams](
	[name] [sysname] NOT NULL
) ON [PRIMARY];
GO
ALTER TABLE [dbo].[DimThing] WITH CHECK ADD
    CONSTRAINT [PK_DimThing_ThingKey] PRIMARY KEY CLUSTERED
    (
        [ThingKey]
    )  ON [PRIMARY];
GO
CREATE UNIQUE NONCLUSTERED INDEX [AK_DimThing_ThingAlternateKey] ON [dbo].[DimThing]([ThingAlternateKey]) ON [PRIMARY];
GO
ALTER TABLE [dbo].[FactThingSales] ADD
    CONSTRAINT [FK_FactThingSales_DimThing]  FOREIGN KEY
    (
        [ThingKey]
    ) REFERENCES [dbo].[DimThing] ([ThingKey]);
GO
"""


def test_names_become_snake_case() -> None:
    assert aw.snake("FactInternetSales") == "fact_internet_sales"
    assert aw.snake("SpanishProductName") == "spanish_product_name"
    assert aw.snake("AddressLine1") == "address_line1"
    assert aw.snake("ProductKey") == "product_key"


def test_tables_get_postgres_types_and_skip_the_ssms_ones() -> None:
    found = aw.tables(SCRIPT)
    assert list(found) == ["DimThing", "FactThingSales"]
    assert found["DimThing"] == [
        ("ThingKey", "integer", True),
        ("ThingAlternateKey", "varchar(25)", False),
        ("SpanishThingName", "varchar(50)", True),
        ("ListPrice", "numeric(19,4)", False),
        ("IsCurrent", "boolean", True),
        ("LargePhoto", "bytea", False),
    ]


def test_keys_come_after_the_load_and_the_double_spaced_foreign_key_is_kept() -> None:
    assert aw.constraints(SCRIPT) == [
        "ALTER TABLE dim_thing ADD CONSTRAINT pk_dim_thing_thing_key PRIMARY KEY (thing_key);",
        "CREATE UNIQUE INDEX ak_dim_thing_thing_alternate_key ON dim_thing (thing_alternate_key);",
        "ALTER TABLE fact_thing_sales ADD CONSTRAINT fk_fact_thing_sales_dim_thing "
        "FOREIGN KEY (thing_key) REFERENCES dim_thing (thing_key);",
    ]


def test_a_field_loads_as_bulk_insert_read_it() -> None:
    # Empty is NULL; one NUL byte is SQL Server's empty string, which PostgreSQL text cannot hold.
    assert aw.cell(b"", "text") == r"\N"
    assert aw.cell(b"\x00", "varchar(50)") == ""
    assert aw.cell(b"474946", "bytea") == r"\\x474946"
    assert aw.cell(b"adventure-works\\guy1", "varchar(256)") == r"adventure-works\\guy1"
    with pytest.raises(ValueError, match="NUL inside"):
        aw.cell(b"a\x00b", "text")


def test_crlf_lines_and_a_short_line_are_told_apart() -> None:
    columns = aw.tables(SCRIPT)["DimThing"]
    data = b"1|AR-1|\x00|1.5|1|474946\r\n2||Rueda|||\r\n"
    assert list(aw.rows(data, "DimThing", columns)) == [
        "1\tAR-1\t\t1.5\t1\t\\\\x474946",
        "2\t\\N\tRueda\t\\N\t\\N\t\\N",
    ]
    with pytest.raises(ValueError, match="line 1 has 2 fields"):
        list(aw.rows(b"1|x\n", "DimThing", columns))


def test_every_file_the_loader_reads_is_pinned_by_commit_and_sha256() -> None:
    spec = json.loads((HERE / "sources.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", spec["commit"])
    files = spec["files"]
    assert "instawdbdw.sql" in files
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in files.values())
    # One file per table the loader creates (28) plus the script.
    assert sum(name.endswith(".csv") for name in files) == 28
    assert "sysdiagrams.csv" not in files
