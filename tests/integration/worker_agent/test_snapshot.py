"""DF-04: a version's table becomes one Parquet file, the same bytes every time for the same rows
and provenance, every page checksummed, and SQL on it shows only what its reader may see."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from libs.snapshot import reader, writer
from libs.snapshot.store import StoreUnavailable

from .conftest import Seed

pytestmark = [pytest.mark.integration]

AVAILABLE = datetime.datetime(2026, 11, 19, 8, 0, tzinfo=datetime.UTC)


class FakeStore:
    def __init__(self, refuse: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.refuse = refuse

    async def put(self, key: str, path: Path) -> bool:
        if self.refuse:
            raise StoreUnavailable("the object store is unreachable: 503")
        if key in self.objects:
            return False
        self.objects[key] = path.read_bytes()
        return True

    async def get(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])


def _table(seed: Seed) -> str:
    name = f"snap_{uuid.uuid4().hex[:8]}"
    with seed.conn.cursor() as cur:
        cur.execute(
            f"CREATE TABLE public.{name} (id int4, amount numeric, price numeric(10,2),"
            " note text, ok boolean, day date, at timestamptz, ratio float8, extra jsonb,"
            " code uuid, huge numeric)"
        )
        cur.execute(
            f"INSERT INTO public.{name} VALUES"
            " (1, 1.5, 10.25, 'a \"quoted\", line\nbreak', true, '2026-01-31',"
            "  '2026-01-31 10:00:00+07', 'NaN', '{\"k\": 1}', 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',"
            "  'Infinity'),"
            " (2, -0.125, NULL, '', false, NULL, NULL, 2.5, NULL, NULL, 1),"
            " (3, NULL, 3, NULL, NULL, '2026-02-01', '2026-02-01 00:00:00+00', NULL, '[]', NULL, 2)"
        )
    return name


async def _write(dsn: str, table: str, store: FakeStore, **extra: Any) -> Any:
    conn = await asyncpg.connect(dsn)
    try:
        options: dict[str, Any] = {
            "schema": "public",
            "table": table,
            "org_id": uuid.UUID(int=1),
            "dataset_id": uuid.UUID(int=2),
            "version": 3,
            "run_id": uuid.UUID(int=4),
            "available_at": AVAILABLE,
            "store": store,
        }
        options.update(extra)
        return await writer.write(conn, **options)
    finally:
        await conn.close()


async def test_a_version_becomes_the_same_parquet_every_time(
    seed: Seed, worker_database: str, tmp_path: Path
) -> None:
    table = _table(seed)
    store = FakeStore()
    first = await _write(worker_database, table, store)
    again = await _write(worker_database, table, store)
    assert isinstance(first, writer.Snapshot) and isinstance(again, writer.Snapshot)
    # Content-addressed: the same rows and provenance are the same bytes, written once.
    assert first.sha256 == again.sha256 and first.created and not again.created
    assert (
        first.object_key == f"{uuid.UUID(int=1)}/{uuid.UUID(int=2)}/v3/{first.sha256[:12]}.parquet"
    )
    other = await _write(worker_database, table, store, run_id=uuid.UUID(int=5))
    assert other.sha256 != first.sha256

    path = tmp_path / "v3.parquet"
    path.write_bytes(store.objects[first.object_key])
    file = pq.ParquetFile(path)
    meta = file.schema_arrow.metadata
    assert meta[b"dataagent.dataset_id"] == str(uuid.UUID(int=2)).encode()
    assert meta[b"dataagent.version"] == b"3"
    assert meta[b"dataagent.available_at"] == b"2026-11-19T08:00:00+00:00"
    assert file.metadata.row_group(0).column(0).compression == "ZSTD"
    types = {f.name: f.type for f in file.schema_arrow}
    assert types["amount"] == pa.decimal128(38, 3) and types["price"] == pa.decimal128(10, 2)
    # Infinity has no decimal: that column stays exact as text.
    assert types["huge"] == pa.string() and types["at"] == pa.timestamp("us", tz="UTC")
    rows = file.read().to_pylist()
    assert rows[0]["note"] == 'a "quoted", line\nbreak' and rows[1]["note"] == ""
    assert rows[2]["note"] is None
    assert rows[0]["amount"] == Decimal("1.500") and rows[1]["amount"] == Decimal("-0.125")
    assert rows[0]["at"] == datetime.datetime(2026, 1, 31, 3, 0, tzinfo=datetime.UTC)
    assert rows[0]["ratio"] != rows[0]["ratio"]  # NaN
    assert rows[0]["huge"] == "Infinity" and rows[0]["extra"] == '{"k": 1}'
    assert [c["pg_type"] for c in first.columns][:3] == ["integer", "numeric", "numeric(10,2)"]


async def test_a_damaged_page_is_found_not_read(
    seed: Seed, worker_database: str, tmp_path: Path
) -> None:
    store = FakeStore()
    done = await _write(worker_database, _table(seed), store)
    data = bytearray(store.objects[done.object_key])
    file = tmp_path / "v.parquet"
    file.write_bytes(data)
    column = pq.ParquetFile(file).metadata.row_group(0).column(3)  # `note`, plain text pages
    # The chunk's last bytes are its last page's payload, past every page header.
    start = column.dictionary_page_offset if column.has_dictionary_page else column.data_page_offset
    data[start + column.total_compressed_size - 2] ^= 0xFF
    file.write_bytes(data)
    with pytest.raises(OSError, match="(?i)crc|checksum|corrupt"):
        pq.read_table(file, page_checksum_verification=True)


async def test_too_many_rows_off_or_no_store_is_skipped_not_failed(
    seed: Seed, worker_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _table(seed)
    over = await _write(worker_database, table, FakeStore(), max_rows=2)
    assert (
        isinstance(over, writer.Skipped)
        and over.row_count == 3
        and "SNAPSHOT_MAX_ROWS" in over.reason
    )
    down = await _write(worker_database, table, FakeStore(refuse=True))
    assert isinstance(down, writer.Skipped) and down.row_count == 3 and "503" in down.reason
    monkeypatch.setattr(writer, "ENABLED", False)
    off = await _write(worker_database, table, FakeStore())
    assert isinstance(off, writer.Skipped) and off.row_count is None


async def test_sql_on_a_snapshot_sees_only_what_its_reader_may(
    seed: Seed, worker_database: str, tmp_path: Path
) -> None:
    with seed.conn.cursor() as cur:
        table = f"snap_{uuid.uuid4().hex[:8]}"
        cur.execute(f"CREATE TABLE public.{table} (branch text, phone text, card text, n int)")
        cur.execute(
            f"INSERT INTO public.{table} VALUES ('HN', '0912345678', '4111222233334444', 1),"
            " ('SG', '0987654321', '5500111122223333', 2)"
        )
    store = FakeStore()
    done = await _write(worker_database, table, store)
    path = tmp_path / "v.parquet"
    path.write_bytes(store.objects[done.object_key])
    files = {"loans": reader.SnapshotFile(path, done.sha256)}

    seen = reader.query(
        files,
        "SELECT * FROM loans ORDER BY n",
        hidden={"loans": ["card"]},
        masks={"loans": {"phone": "last4"}},
    )
    assert seen.columns == ["branch", "phone", "n"]
    assert seen.rows == [("HN", "****5678", 1), ("SG", "****4321", 2)]
    hashed = reader.query(files, "SELECT phone FROM loans", masks={"loans": {"phone": "hash"}})
    assert all(len(r[0]) == 32 for r in hashed.rows)
    assert reader.query(files, "SELECT n FROM loans", max_rows=1).truncated

    # The SQL names the masked table and nothing else: not the file, whose columns are unmasked.
    escaped = str(path).replace("'", "''")
    with pytest.raises(duckdb.Error):
        reader.query(
            files, f"SELECT card FROM read_parquet('{escaped}')", hidden={"loans": ["card"]}
        )
    with pytest.raises(duckdb.Error):
        reader.query(files, "SET enable_external_access = true")
    path.write_bytes(store.objects[done.object_key] + b"\0")
    with pytest.raises(reader.SnapshotMismatch):
        reader.query(files, "SELECT 1")


@pytest.mark.performance
def test_two_million_adventureworks_rows_within_the_budget(
    request: pytest.FixtureRequest,
) -> None:
    # PoC-2 (DATA_PIPELINE_SPEC §1 C): FactInternetSales (60 398 rows) times 34 ≈ 2 053 532 rows in
    # ≤ 120 s; the numbers go to model/eval/snapshot-poc2.md. A minute and the AdventureWorks
    # load, so it runs only when asked: PYTEST_ARGS="-m performance <this file>".
    if "performance" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("PoC-2 runs with -m performance")
    adventureworks = request.getfixturevalue("adventureworks")
    with adventureworks.cursor() as cur:
        # Each copy its own: an order number and a key no other row has, so the file is as large
        # as two million distinct rows make it, not a copy compressed away.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS fact_2m AS SELECT f.*, g AS copy_no,"
            " f.sales_order_number || '-' || g AS order_copy,"
            " md5(f.sales_order_number || f.sales_order_line_number || g) AS row_key"
            " FROM fact_internet_sales f, generate_series(1, 34) g"
        )
    admin = (
        os.environ.get("DATAAGENT_TEST_ADMIN_DSN")
        or request.getfixturevalue("postgres_container")["dsn"]
    )
    dsn = admin.rsplit("/", 1)[0] + "/" + adventureworks.info.dbname
    # The writer alone, in a process of its own, so its peak memory is its own.
    out = subprocess.run(
        [sys.executable, "-c", _BENCH, dsn], capture_output=True, text=True, check=True, timeout=600
    )
    measured = json.loads(out.stdout.strip().splitlines()[-1])
    print(f"\nPoC-2: {measured}")
    assert measured["rows"] == 60_398 * 34 and measured["seconds"] <= 120
    # worker-ingest is limited to 256Mi; the writer alone stays under it.
    assert measured["peak_rss_mib"] <= 256


_BENCH = """
import asyncio, datetime, json, sys, time, uuid
from pathlib import Path
import asyncpg
from libs.snapshot import writer

def hwm():
    # VmHWM starts again at exec; ru_maxrss would keep the forking pytest's peak.
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024)

class Store:
    async def put(self, key, path):
        self.size = path.stat().st_size
        return True

async def main(dsn):
    conn = await asyncpg.connect(dsn)
    store = Store()
    started = time.monotonic()
    done = await writer.write(
        conn, schema="public", table="fact_2m", org_id=uuid.UUID(int=1), dataset_id=uuid.UUID(int=2),
        version=1, run_id=None, available_at=datetime.datetime.now(datetime.UTC), store=store,
    )
    seconds = time.monotonic() - started
    await conn.close()
    assert isinstance(done, writer.Snapshot), done
    print(json.dumps({
        "rows": done.row_count, "columns": len(done.columns), "mb": round(done.byte_size / 1e6, 1),
        "seconds": round(seconds, 1),
        "peak_rss_mib": hwm(),
        "row_group_rows": writer.ROW_GROUP_ROWS,
    }))

asyncio.run(main(sys.argv[1]))
"""
