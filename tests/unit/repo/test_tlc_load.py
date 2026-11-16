"""`make tlc` (tests/e2e/tlc/load.py): every file it reads is pinned, the months load as published
into one schema across years, and the stream is one transaction the reader can only read."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import pathlib
import re
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]

HERE = pathlib.Path(__file__).parents[2] / "e2e" / "tlc"
_spec = importlib.util.spec_from_file_location("tlc_load", HERE / "load.py")
assert _spec is not None and _spec.loader is not None
tlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tlc)


def test_every_file_is_pinned_to_the_publisher() -> None:
    spec = tlc.spec()
    assert spec["snapshot"] == "TLC-2024Q1"
    for name, item in spec["files"].items():
        assert re.fullmatch(r"[a-z0-9_.-]+", name), name
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), name
        assert item["bytes"] > 0
        assert item["url"].startswith(
            ("https://d37ci6vzurychx.cloudfront.net/", "https://www.nyc.gov/assets/tlc/")
        ), name
        assert item["url"].rsplit("/", 1)[1] == name


def test_the_snapshot_is_three_yellow_months_and_two_years_of_green() -> None:
    assert tlc.months("yellow_tripdata_") == [
        f"yellow_tripdata_2024-0{m}.parquet" for m in (1, 2, 3)
    ]
    assert tlc.months("green_tripdata_") == [
        f"green_tripdata_{y}-0{m}.parquet" for y in (2023, 2024) for m in (1, 2, 3)
    ]


def _write(path: pathlib.Path, columns: dict[str, Any]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table(columns), path)


@pytest.fixture
def cache(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    import datetime as dt

    import pyarrow as pa

    when = [dt.datetime(2024, 1, 1, 0, 57, 55), dt.datetime(2002, 12, 31, 22, 59, 39)]
    # Yellow as 2024 publishes it, without Airport_fee to stand for a column a month lacks.
    _write(
        tmp_path / "yellow_tripdata_2024-01.parquet",
        {
            "VendorID": pa.array([2, 1], pa.int32()),
            "tpep_pickup_datetime": when,
            "tpep_dropoff_datetime": when,
            "passenger_count": pa.array([1, None], pa.int64()),
            "trip_distance": [1.72, 0.0],
            "RatecodeID": pa.array([1, None], pa.int64()),
            "store_and_fwd_flag": ["N", None],
            "PULocationID": pa.array([186, 264], pa.int32()),
            "DOLocationID": pa.array([79, 264], pa.int32()),
            "payment_type": pa.array([2, 0], pa.int64()),
            "fare_amount": [17.7, -5.0],
            "extra": [1.0, 0.0],
            "mta_tax": [0.5, -0.5],
            "tip_amount": [0.0, 0.0],
            "tolls_amount": [0.0, 0.0],
            "improvement_surcharge": [1.0, -1.0],
            "total_amount": [22.7, -6.5],
            "congestion_surcharge": [2.5, 0.0],
        },
    )
    # Green as 2023 publishes it: doubles where 2024 has integers, an all-null ehail_fee.
    _write(
        tmp_path / "green_tripdata_2023-01.parquet",
        {
            "VendorID": pa.array([2], pa.int64()),
            "lpep_pickup_datetime": when[:1],
            "lpep_dropoff_datetime": when[:1],
            "store_and_fwd_flag": ["N"],
            "RatecodeID": [1.0],
            "PULocationID": pa.array([166], pa.int64()),
            "DOLocationID": pa.array([143], pa.int64()),
            "passenger_count": [1.0],
            "trip_distance": [2.58],
            "fare_amount": [14.9],
            "extra": [1.0],
            "mta_tax": [0.5],
            "tip_amount": [4.03],
            "tolls_amount": [0.0],
            "ehail_fee": pa.nulls(1),
            "improvement_surcharge": [1.0],
            "total_amount": [24.18],
            "payment_type": [1.0],
            "trip_type": [1.0],
            "congestion_surcharge": [2.75],
        },
    )
    (tmp_path / "taxi_zone_lookup.csv").write_bytes(
        b'"LocationID","Borough","Zone","service_zone"\r\n1,"EWR","Newark Airport","EWR"\r\n'
    )
    files = {
        "yellow_tripdata_2024-01.parquet": {},
        "green_tripdata_2023-01.parquet": {},
        "taxi_zone_lookup.csv": {},
    }
    monkeypatch.setattr(tlc, "spec", lambda: {"files": files})
    return tmp_path


def _sql(cache: pathlib.Path, reader: str | None = "app_user") -> str:
    return b"".join(tlc.stream(cache, reader)).decode()


def test_the_stream_is_one_transaction_that_stops_on_error(cache: pathlib.Path) -> None:
    sql = _sql(cache)
    assert sql.startswith("\\set ON_ERROR_STOP on\n")
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1
    assert sql.index("COMMIT;") > sql.rindex("\\.\n")


def test_rows_load_as_published_with_their_file(cache: pathlib.Path) -> None:
    sql = _sql(cache)
    block = sql.split("COPY yellow_trip (")[1].split("\\.\n")[0]
    header, *rows = block.splitlines()
    assert header.endswith("source_file) FROM STDIN WITH (FORMAT csv);")
    assert len(rows) == 2
    # The 2002 pickup and the negative total stay: the raw data is never corrected.
    assert rows[1].startswith("1,2002-12-31 22:59:39")
    assert "-6.5" in rows[1]
    # A field the month lacks is null, the file's name closes every row.
    assert rows[0].endswith(',2.5,,"yellow_tripdata_2024-01.parquet"')


def test_green_2023_doubles_load_as_integers(cache: pathlib.Path) -> None:
    sql = _sql(cache)
    row = sql.split("COPY green_trip (")[1].split("\\.\n")[0].splitlines()[1]
    when = "2024-01-01 00:57:55.000000"
    assert row.startswith(f'2,{when},{when},"N",1,166,143,1,2.58,')
    assert ",,1,24.18,1,1,2.75," in row  # ehail_fee null, payment_type and trip_type integers


def test_zone_lines_lose_their_carriage_returns(cache: pathlib.Path) -> None:
    block = _sql(cache).split("COPY taxi_zone FROM STDIN WITH (FORMAT csv, HEADER true);\n")[1]
    assert block.startswith('"LocationID"') and "\r" not in block.split("\\.\n")[0]


def test_code_tables_are_the_dictionaries_and_keys_are_not_validated(cache: pathlib.Path) -> None:
    sql = _sql(cache)
    assert "INSERT INTO payment_type_code VALUES (0, 'Flex Fare trip'), (1, 'Credit card')" in sql
    assert "(99, 'Null/unknown')" in sql
    keys = re.findall(
        r"ALTER TABLE (\w+) ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\)[^;]*NOT VALID;", sql
    )
    assert ("yellow_trip", "pu_location_id") in keys and ("green_trip", "trip_type") in keys
    # One key to taxi_zone per fact: two would be two equally short join paths.
    assert not any(column == "do_location_id" for _, column in keys)
    assert sql.count("FOREIGN KEY") == len(keys)


def test_the_reader_only_reads(cache: pathlib.Path) -> None:
    sql = _sql(cache)
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "app_user";' in sql
    assert not re.search(r"GRANT (?!USAGE|SELECT)", sql)
    assert "GRANT" not in _sql(cache, reader=None)


def test_literals_are_quoted() -> None:
    assert tlc._lit("Creative Mobile Technologies, LLC") == "'Creative Mobile Technologies, LLC'"
    assert tlc._lit("O'Hare") == "'O''Hare'"


def test_fetch_refuses_a_file_whose_hash_differs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = b"published bytes"
    files = {
        "taxi_zone_lookup.csv": {
            "url": "https://x/taxi_zone_lookup.csv",
            "sha256": hashlib.sha256(good).hexdigest(),
        }
    }
    monkeypatch.setattr(tlc, "spec", lambda: {"files": files})
    monkeypatch.setattr(tlc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"changed bytes"))
    with pytest.raises(SystemExit, match="sha256 differs"):
        tlc.fetch(tmp_path)
    assert not (tmp_path / "taxi_zone_lookup.csv").exists()

    monkeypatch.setattr(tlc.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(good))
    tlc.fetch(tmp_path)
    assert (tmp_path / "taxi_zone_lookup.csv").read_bytes() == good
