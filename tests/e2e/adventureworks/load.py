"""AdventureWorksDW, the dataset the product is tested on, as one psql stream for PostgreSQL.

Microsoft ships it as a SQL Server install script (instawdbdw.sql) and pipe-delimited files.
The product reads PostgreSQL, so this turns that script into PostgreSQL DDL and the files into
COPY blocks, with the tables and columns in snake_case (FactInternetSales.SalesAmount →
fact_internet_sales.sales_amount):

    python -I load.py fetch --cache DIR          download sources.json's files, check sha256
    python -I load.py sql --cache DIR [--reader app_user] | psql -d adventureworks_dw

`make adventureworks` runs both against the cluster. The stream is one transaction: it drops and
recreates every table, so a load that fails leaves the previous one in place.

What BULK INSERT does implicitly and COPY does not, done here:
  - an empty field is NULL, a field of one NUL byte is the empty string (DimProduct's Spanish and
    French names); PostgreSQL rejects NUL in text
  - DimProduct.csv ends its lines with CRLF, the other files with LF
  - varbinary columns (photos) are bare hex, bytea wants a \\x prefix
  - backslashes (DimEmployee logins 'adventure-works\\guy1') must be escaped for COPY text
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path

SOURCES = Path(__file__).with_name("sources.json")

# SSMS diagrams, the DDL-trigger log and the build-version stamp: no business data.
SKIP = {"sysdiagrams", "DatabaseLog", "AdventureWorksDWBuildVersion"}

TYPES = {
    "int": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "smallint",
    "bit": "boolean",
    "money": "numeric(19,4)",
    "float": "double precision",
    "real": "real",
    "datetime": "timestamp",
    "date": "date",
    "xml": "xml",
    "sysname": "varchar(128)",
}

_COLUMN = re.compile(r"^\s*\[(\w+)\]\s+\[(\w+)\](?:\((\w+)\))?.*?(NOT NULL|NULL)?\s*,?\s*$")
_TABLE = re.compile(r"CREATE TABLE \[dbo\]\.\[(\w+)\]\((.*?)\n\) ON", re.S)
_KEY = re.compile(
    r"ALTER TABLE \[dbo\]\.\[(\w+)\][^;]*?CONSTRAINT \[(\w+)\] (PRIMARY KEY|UNIQUE)"
    r"\s+(?:NON)?CLUSTERED\s*\(([^)]*)\)"
)
_UNIQUE_INDEX = re.compile(
    r"CREATE UNIQUE NONCLUSTERED INDEX \[(\w+)\] ON \[dbo\]\.\[(\w+)\]\(([^)]*)\)"
)
# The script writes one constraint with two spaces before FOREIGN KEY.
_FOREIGN_KEY = re.compile(
    r"CONSTRAINT \[(\w+)\]\s+FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES \[dbo\]\.\[(\w+)\]\s*\(([^)]*)\)"
)
_ALTER = re.compile(r"ALTER TABLE \[dbo\]\.\[(\w+)\]")

Column = tuple[str, str, bool]  # name, PostgreSQL type, NOT NULL


def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s).lower()


def pg_type(sql_server_type: str, size: str | None) -> str:
    if sql_server_type in ("nvarchar", "varchar"):
        return "text" if size == "max" else f"varchar({size})"
    if sql_server_type in ("nchar", "char"):
        return f"char({size})"
    if sql_server_type == "varbinary":
        return "bytea"
    return TYPES[sql_server_type]


def tables(script: str) -> dict[str, list[Column]]:
    out: dict[str, list[Column]] = {}
    for m in _TABLE.finditer(script):
        name, body = m.group(1), m.group(2)
        if name in SKIP:
            continue
        columns = []
        for line in body.strip().splitlines():
            c = _COLUMN.match(line)
            if not c:
                raise ValueError(f"unparsed column in {name}: {line!r}")
            columns.append((c.group(1), pg_type(c.group(2), c.group(3)), c.group(4) == "NOT NULL"))
        out[name] = columns
    return out


def _names(bracketed: str) -> str:
    return ", ".join(snake(c) for c in re.findall(r"\[(\w+)\]", bracketed))


def constraints(script: str) -> list[str]:
    """Keys and foreign keys, added after the load like the original script does."""
    out = []
    for table, name, kind, cols in _KEY.findall(script):
        if table not in SKIP:
            out.append(
                f"ALTER TABLE {snake(table)} ADD CONSTRAINT {snake(name)} {kind} ({_names(cols)});"
            )
    for name, table, cols in _UNIQUE_INDEX.findall(script):
        if table not in SKIP:
            out.append(f"CREATE UNIQUE INDEX {snake(name)} ON {snake(table)} ({_names(cols)});")
    for m in _FOREIGN_KEY.finditer(script):
        name, cols, ref, ref_cols = m.groups()
        # The script opens each foreign key with the ALTER TABLE just before it.
        table = _ALTER.findall(script, 0, m.start())[-1]
        out.append(
            f"ALTER TABLE {snake(table)} ADD CONSTRAINT {snake(name)} FOREIGN KEY ({_names(cols)}) "
            f"REFERENCES {snake(ref)} ({_names(ref_cols)});"
        )
    return out


def cell(value: bytes, pg: str) -> str:
    """One field as COPY text."""
    if value == b"":
        return r"\N"
    if value == b"\x00":
        return ""
    if b"\x00" in value:
        raise ValueError(f"NUL inside a field: {value[:40]!r}")
    if pg == "bytea":
        return r"\\x" + value.decode("ascii")
    text = value.decode("utf-8")
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def rows(data: bytes, table: str, columns: list[Column]) -> Iterator[str]:
    for n, line in enumerate(data.split(b"\n"), start=1):
        line = line.removesuffix(b"\r")
        if not line:
            continue
        fields = line.split(b"|")
        if len(fields) != len(columns):
            raise ValueError(
                f"{table}: line {n} has {len(fields)} fields, the table {len(columns)}"
            )
        yield "\t".join(cell(v, t) for v, (_, t, _) in zip(fields, columns, strict=True))


def stream(cache: Path, reader: str | None) -> Iterator[str]:
    script = (cache / "instawdbdw.sql").read_text(encoding="utf-8")
    found = tables(script)
    # Warnings only: a first load would print a "does not exist, skipping" per DROP.
    yield "\\set ON_ERROR_STOP on\nSET client_min_messages = warning;\nBEGIN;\n"
    for name, columns in found.items():
        body = ",\n".join(f"    {snake(c)} {t}{' NOT NULL' if nn else ''}" for c, t, nn in columns)
        yield f"DROP TABLE IF EXISTS {snake(name)} CASCADE;\nCREATE TABLE {snake(name)} (\n{body}\n);\n"
    for name, columns in found.items():
        yield f"COPY {snake(name)} FROM STDIN;\n"
        for line in rows((cache / f"{name}.csv").read_bytes(), name, columns):
            yield line + "\n"
        yield "\\.\n"
    yield "\n".join(constraints(script)) + "\n"
    if reader:
        # The demo source logs in as this role (OpenBao demo-postgres): it reads, nothing more.
        yield (
            f'GRANT USAGE ON SCHEMA public TO "{reader}";\n'
            f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{reader}";\n'
        )
    yield "COMMIT;\nANALYZE;\n"


def fetch(cache: Path) -> None:
    spec = json.loads(SOURCES.read_text())
    cache.mkdir(parents=True, exist_ok=True)
    for name, digest in spec["files"].items():
        target = cache / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            continue
        url = f"{spec['base_url']}/{spec['commit']}/{spec['path']}/{name}"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f"{name}: sha256 differs from sources.json, not loading it")
        target.write_bytes(data)
        print(f"fetched {name} ({len(data)} bytes)", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("fetch", "sql"):
        p = sub.add_parser(command)
        p.add_argument("--cache", type=Path, required=True)
        if command == "sql":
            p.add_argument("--reader", help="role granted SELECT on every table")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        fetch(args.cache)
    else:
        sys.stdout.writelines(stream(args.cache, args.reader))


if __name__ == "__main__":
    main()
