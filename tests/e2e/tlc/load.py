"""NYC TLC Trip Record Data, the real dataset of the DS-TLC Data Space, as one psql stream.

The real-data evaluation (docs/blueprint/DATASET_AND_EVALUATION.md §2) needs records nobody made
up, reachable from the machines the product is built on, fixed under their gold answers. The TLC
publishes each month of trips once, as a Parquet file with a fixed Last-Modified; the snapshot is
those files pinned by sha256 in sources.json (TLC-2024Q1: yellow trips January to March 2024,
green trips January to March 2023 and 2024 for year-on-year), the taxi zone table and the two
data dictionaries:

    python -I load.py fetch --cache DIR             download sources.json's files, check sha256
    python load.py sql --cache DIR [--reader app_user] | psql -d tlc_trips

`make tlc` runs both against the cluster. The stream is one transaction: it drops and recreates
every table, so a load that fails leaves the previous one in place.

The rows are loaded as published, bad ones included (a pickup in 2002 in the January 2024 file,
negative totals, zero distances, payment_type 0 with no rate code): they are what the data quality
checks and the eval's traps are about, and the raw data is never corrected (invariant 2). Each row
keeps the file it came from. The code tables are the data dictionaries' own (dated 18 March 2025,
which also describe cbd_congestion_fee, a 2025 column these 2024 files do not have).

`fetch` is standard library only; `sql` reads Parquet with pyarrow (the dev-tools image has it).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.json"
USER_AGENT = "DataAgent-eval/1.0 (TLC snapshot for internal evaluation)"

# (column, PostgreSQL type, the Parquet field it is read from). Money is numeric(12, 2): the files
# carry doubles, and a sum of doubles is not the total a person adds up.
MONEY = "numeric(12, 2)"
YELLOW: list[tuple[str, str, str]] = [
    ("vendor_id", "integer", "VendorID"),
    ("tpep_pickup_datetime", "timestamp", "tpep_pickup_datetime"),
    ("tpep_dropoff_datetime", "timestamp", "tpep_dropoff_datetime"),
    ("passenger_count", "integer", "passenger_count"),
    ("trip_distance", "double precision", "trip_distance"),
    ("ratecode_id", "integer", "RatecodeID"),
    ("store_and_fwd_flag", "text", "store_and_fwd_flag"),
    ("pu_location_id", "integer", "PULocationID"),
    ("do_location_id", "integer", "DOLocationID"),
    ("payment_type", "integer", "payment_type"),
    ("fare_amount", MONEY, "fare_amount"),
    ("extra", MONEY, "extra"),
    ("mta_tax", MONEY, "mta_tax"),
    ("tip_amount", MONEY, "tip_amount"),
    ("tolls_amount", MONEY, "tolls_amount"),
    ("improvement_surcharge", MONEY, "improvement_surcharge"),
    ("total_amount", MONEY, "total_amount"),
    ("congestion_surcharge", MONEY, "congestion_surcharge"),
    ("airport_fee", MONEY, "Airport_fee"),
]
GREEN: list[tuple[str, str, str]] = [
    ("vendor_id", "integer", "VendorID"),
    ("lpep_pickup_datetime", "timestamp", "lpep_pickup_datetime"),
    ("lpep_dropoff_datetime", "timestamp", "lpep_dropoff_datetime"),
    ("store_and_fwd_flag", "text", "store_and_fwd_flag"),
    ("ratecode_id", "integer", "RatecodeID"),
    ("pu_location_id", "integer", "PULocationID"),
    ("do_location_id", "integer", "DOLocationID"),
    ("passenger_count", "integer", "passenger_count"),
    ("trip_distance", "double precision", "trip_distance"),
    ("fare_amount", MONEY, "fare_amount"),
    ("extra", MONEY, "extra"),
    ("mta_tax", MONEY, "mta_tax"),
    ("tip_amount", MONEY, "tip_amount"),
    ("tolls_amount", MONEY, "tolls_amount"),
    ("ehail_fee", MONEY, "ehail_fee"),
    ("improvement_surcharge", MONEY, "improvement_surcharge"),
    ("total_amount", MONEY, "total_amount"),
    ("payment_type", "integer", "payment_type"),
    ("trip_type", "integer", "trip_type"),
    ("congestion_surcharge", MONEY, "congestion_surcharge"),
]
FACTS = {"yellow_trip": ("yellow_tripdata_", YELLOW), "green_trip": ("green_tripdata_", GREEN)}

# The data dictionaries' codes (data_dictionary_trip_records_{yellow,green}.pdf, 18 March 2025).
CODES: dict[str, tuple[str, list[tuple[int, str]]]] = {
    "payment_type_code": (
        "How the passenger paid (payment_type).",
        [
            (0, "Flex Fare trip"),
            (1, "Credit card"),
            (2, "Cash"),
            (3, "No charge"),
            (4, "Dispute"),
            (5, "Unknown"),
            (6, "Voided trip"),
        ],
    ),
    "rate_code": (
        "The final rate code in effect at the end of the trip (ratecode_id).",
        [
            (1, "Standard rate"),
            (2, "JFK"),
            (3, "Newark"),
            (4, "Nassau or Westchester"),
            (5, "Negotiated fare"),
            (6, "Group ride"),
            (99, "Null/unknown"),
        ],
    ),
    "vendor": (
        "The technology provider that sent the record (vendor_id).",
        [
            (1, "Creative Mobile Technologies, LLC"),
            (2, "Curb Mobility, LLC"),
            (6, "Myle Technologies Inc"),
            (7, "Helix"),
        ],
    ),
    "trip_type_code": (
        "Street-hail or dispatch, green trips only (trip_type).",
        [(1, "Street-hail"), (2, "Dispatch")],
    ),
}
# (table, column, code table): declared NOT VALID, the published rows are not checked against
# them (a code outside a dictionary stays, it is a finding), but the scan reads them as joins.
# Only the pickup zone is a key: two keys to taxi_zone would be two equally short join paths,
# which the metric compiler refuses; the dropoff zone is a column a question filters on.
CODE_KEYS = [
    (fact, column, table)
    for fact in FACTS
    for column, table in (
        ("payment_type", "payment_type_code"),
        ("ratecode_id", "rate_code"),
        ("vendor_id", "vendor"),
        ("pu_location_id", "taxi_zone"),
    )
] + [("green_trip", "trip_type", "trip_type_code")]
COMMENTS = {
    "yellow_trip": "Yellow taxi trips (TLC trip records), one row per trip as published.",
    "green_trip": "Green taxi (street hail livery) trips (TLC trip records), one row per trip as published.",
    "taxi_zone": "TLC taxi zones (taxi_zone_lookup.csv).",
}
COLUMN_COMMENTS = {
    "tip_amount": "Tip amount, populated for credit card tips; cash tips are not included.",
    "total_amount": "The total amount charged to passengers; does not include cash tips.",
    "pu_location_id": "TLC taxi zone in which the taximeter was engaged.",
    "do_location_id": "TLC taxi zone in which the taximeter was disengaged.",
}


def spec() -> dict[str, Any]:
    out: dict[str, Any] = json.loads(SOURCES.read_text())
    return out


def fetch(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for name, item in spec()["files"].items():
        target = cache / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            continue
        request = urllib.request.Request(item["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=600) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise SystemExit(f"{name}: sha256 differs from sources.json, not loading it")
        target.write_bytes(data)
        print(f"fetched {name} ({len(data)} bytes)", file=sys.stderr)


def months(prefix: str) -> list[str]:
    return sorted(n for n in spec()["files"] if n.startswith(prefix) and n.endswith(".parquet"))


def _create(table: str, columns: list[tuple[str, str, str]]) -> str:
    # A trip record has no key of its own (two identical rows are two trips as far as anyone can
    # tell); the semantic model needs one, so the load numbers the rows in file order.
    body = ",\n".join(f"    {c} {t}" for c, t, _ in columns)
    return (
        f"DROP TABLE IF EXISTS {table} CASCADE;\n"
        f"CREATE TABLE {table} (\n    trip_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n"
        f"{body},\n    source_file text NOT NULL\n);\n"
    )


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _csv(path: Path, columns: list[tuple[str, str, str]]) -> Iterator[bytes]:
    """One month as CSV for COPY, a batch at a time (a yellow month is ~3 million rows): the
    target columns cast to one type across years (green 2023 has doubles where 2024 has integers,
    and an all-null ehail_fee), a field a file lacks as nulls, then the file's name."""
    import pyarrow as pa
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    arrow = {"integer": pa.int32(), "timestamp": pa.timestamp("us"), "text": pa.string()}
    names = [c for c, _, _ in columns] + ["source_file"]
    parquet = pq.ParquetFile(path)
    present = [f for _, _, f in columns if f in parquet.schema_arrow.names]
    for batch in parquet.iter_batches(batch_size=200_000, columns=present):
        out = []
        for _, pg, field in columns:
            target = arrow.get(pg, pa.float64())
            if field in batch.schema.names:
                out.append(batch.column(field).cast(target))
            else:
                out.append(pa.nulls(batch.num_rows, target))
        out.append(pa.repeat(path.name, batch.num_rows))
        buf = io.BytesIO()
        pacsv.write_csv(pa.table(out, names=names), buf, pacsv.WriteOptions(include_header=False))
        yield buf.getvalue()


def stream(cache: Path, reader: str | None) -> Iterator[bytes]:
    yield b"\\set ON_ERROR_STOP on\nSET client_min_messages = warning;\nBEGIN;\n"
    for table, (_, columns) in FACTS.items():
        yield _create(table, columns).encode()
    yield (
        b"DROP TABLE IF EXISTS taxi_zone CASCADE;\n"
        b"CREATE TABLE taxi_zone (location_id integer PRIMARY KEY, borough text, zone text,"
        b" service_zone text);\n"
    )
    for table, (comment, codes) in CODES.items():
        values = ", ".join(f"({code}, {_lit(name)})" for code, name in codes)
        yield (
            f"DROP TABLE IF EXISTS {table} CASCADE;\n"
            f"CREATE TABLE {table} (code integer PRIMARY KEY, name text NOT NULL);\n"
            f"INSERT INTO {table} VALUES {values};\n"
            f"COMMENT ON TABLE {table} IS {_lit(comment)};\n"
        ).encode()
    yield b"COPY taxi_zone FROM STDIN WITH (FORMAT csv, HEADER true);\n"
    # The published file ends its lines with CRLF; COPY reads a stray CR before \. as data.
    zones = (cache / "taxi_zone_lookup.csv").read_bytes().replace(b"\r\n", b"\n")
    yield zones.rstrip(b"\n") + b"\n\\.\n"
    for table, (prefix, columns) in FACTS.items():
        names = ", ".join([c for c, _, _ in columns] + ["source_file"])
        for name in months(prefix):
            yield f"COPY {table} ({names}) FROM STDIN WITH (FORMAT csv);\n".encode()
            yield from _csv(cache / name, columns)
            yield b"\\.\n"
    for table, column, target in CODE_KEYS:
        key = "location_id" if target == "taxi_zone" else "code"
        yield (
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_fkey FOREIGN KEY ({column})"
            f" REFERENCES {target} ({key}) NOT VALID;\n"
        ).encode()
    for table, comment in COMMENTS.items():
        yield f"COMMENT ON TABLE {table} IS {_lit(comment)};\n".encode()
    for table in FACTS:
        for column, comment in COLUMN_COMMENTS.items():
            yield f"COMMENT ON COLUMN {table}.{column} IS {_lit(comment)};\n".encode()
    yield (
        b"CREATE INDEX yellow_trip_pickup_idx ON yellow_trip (tpep_pickup_datetime);\n"
        b"CREATE INDEX green_trip_pickup_idx ON green_trip (lpep_pickup_datetime);\n"
    )
    if reader:
        # The demo source logs in as this role (OpenBao demo-postgres): it reads, nothing more.
        yield (
            f'GRANT USAGE ON SCHEMA public TO "{reader}";\n'
            f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{reader}";\n'
        ).encode()
    yield b"COMMIT;\nANALYZE;\n"


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
        for chunk in stream(args.cache, args.reader):
            sys.stdout.buffer.write(chunk)


if __name__ == "__main__":
    main()
