"""Closing a period for real (UC-10-02): the snapshot as of the business date with its hashes,
the reconciliation on the captured rows, the period closed or close_failed."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import periods, storage
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


class Store:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        assert (bucket, key) not in self.objects, "a snapshot file was rewritten"
        self.objects[(bucket, key)] = body


async def drain() -> None:
    source = OutboxTableSource(schema="period", event_types=frozenset(periods.HANDLERS))
    while await run_once(source, periods.HANDLERS, batch=10):
        pass


def close(bank: Bank, ledger: int, monkeypatch: pytest.MonkeyPatch) -> tuple[uuid.UUID, Store]:
    seed = bank.seed
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.dataset SET business_date_col = 'as_of_date', history_kind = 'snapshot'"
            " WHERE id = %s",
            (str(bank.loans),),
        )
        cal = uuid.uuid4()
        cur.execute(
            "INSERT INTO period.period_calendar (id, org_id, data_space_id, period_kind, close_rule,"
            " dataset_ids, reconciliation_items) VALUES (%s,%s,%s,'month','ngày 3',%s::uuid[],%s)",
            (
                str(cal),
                str(bank.org),
                str(bank.space),
                [str(bank.loans)],
                json.dumps(
                    [
                        {
                            "code": "tong_du_no",
                            "dataset_id": str(bank.loans),
                            "aggregate": "sum",
                            "column": "balance",
                            "ledger": {"value": ledger},
                            "tolerance": 1,
                        },
                        {
                            "code": "so_khoan_vay",
                            "dataset_id": str(bank.loans),
                            "aggregate": "count",
                            "ledger": {"value": 6},
                            "tolerance": 0,
                        },
                    ]
                ),
            ),
        )
    pid = seed.row(
        "period.period_close",
        org_id=bank.org,
        data_space_id=bank.space,
        period_calendar_id=cal,
        period_label="2026-09",
        business_date="2026-09-30",
        status="closing",
        closed_by=bank.user,
    )
    seed.row(
        "period.outbox",
        org_id=bank.org,
        aggregate_type="period_close",
        aggregate_id=pid,
        event_type="period.close_requested",
        payload=json.dumps({"period_close_id": str(pid), "data_space_id": str(bank.space)}),
    )
    store = Store()
    monkeypatch.setattr(storage, "_store", store)
    return pid, store


@pytest.mark.req("AC-10-02-01")
async def test_a_period_closes_with_its_snapshot_hashed(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, store = close(bank, 8000, monkeypatch)
    await drain()
    status, snapshot_hash = seed.one(
        "SELECT status, snapshot_hash FROM period.period_close WHERE id = %s", pid
    )
    assert status == "closed" and snapshot_hash
    rows, sha, key = seed.one(
        "SELECT row_count, sha256, object_key FROM period.period_snapshot_file"
        " WHERE period_close_id = %s",
        pid,
    )
    # 30/09 only: the 31/08 balance is another date's.
    assert rows == 6
    body = store.objects[(periods.BUCKET, key)]
    assert hashlib.sha256(body).hexdigest() == sha
    assert len(gzip.decompress(body).decode().splitlines()) == 6
    assert dict(
        seed.all(
            "SELECT item_code, status FROM period.period_reconciliation WHERE period_close_id = %s",
            pid,
        )
    ) == {"tong_du_no": "matched", "so_khoan_vay": "matched"}


@pytest.mark.req("AC-10-02-03")
async def test_a_ledger_mismatch_fails_the_close(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, _ = close(bank, 7000, monkeypatch)
    await drain()
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "close_failed"
    status, delta = seed.one(
        "SELECT status, delta FROM period.period_reconciliation"
        " WHERE period_close_id = %s AND item_code = 'tong_du_no'",
        pid,
    )
    assert status == "mismatched" and delta == 1000


def again(bank: Bank, pid: uuid.UUID) -> None:
    """The close asked once more, as after a failure."""
    bank.seed.conn.cursor().execute(
        "UPDATE period.period_close SET status = 'closing' WHERE id = %s", (str(pid),)
    )
    bank.seed.row(
        "period.outbox",
        org_id=bank.org,
        aggregate_type="period_close",
        aggregate_id=pid,
        event_type="period.close_requested",
        payload=json.dumps({"period_close_id": str(pid), "data_space_id": str(bank.space)}),
    )


@pytest.mark.req("AC-10-02-05")
async def test_a_retry_keeps_the_files_already_written(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, store = close(bank, 8000, monkeypatch)
    await drain()
    first = seed.one("SELECT snapshot_hash FROM period.period_close WHERE id = %s", pid)
    again(bank, pid)
    # The Store refuses a second write of the same file; the rows are the same, so the hash is.
    await drain()
    assert len(store.objects) == 1
    status, snapshot_hash = seed.one(
        "SELECT status, snapshot_hash FROM period.period_close WHERE id = %s", pid
    )
    assert (status, snapshot_hash) == ("closed", first)


async def test_a_retry_on_changed_rows_does_not_close(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, store = close(bank, 8000, monkeypatch)
    # A file written by an earlier attempt, on rows that are no longer what the source holds.
    seed.row(
        "period.period_snapshot_file",
        org_id=bank.org,
        period_close_id=pid,
        dataset_id=bank.loans,
        dataset_version=1,
        object_key="earlier",
        row_count=6,
        sha256="a" * 64,
    )
    await drain()
    assert store.objects == {}
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "close_failed"


@pytest.mark.parametrize(("approved_delta", "closed"), [(1000, "closed"), (500, "close_failed")])
async def test_an_approved_exception_stands_only_for_its_variance(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    approved_delta: int,
    closed: str,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, _ = close(bank, 7000, monkeypatch)
    seed.row(
        "period.period_reconciliation",
        org_id=bank.org,
        period_close_id=pid,
        item_code="tong_du_no",
        snapshot_value=7000 + approved_delta,
        ledger_value=7000,
        tolerance=1,
        delta=approved_delta,
        status="exception_approved",
        hitl_request_id=uuid.uuid4(),
    )
    await drain()
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == closed
    status, delta = seed.one(
        "SELECT status, delta FROM period.period_reconciliation"
        " WHERE period_close_id = %s AND item_code = 'tong_du_no'",
        pid,
    )
    # A variance that moved needs its own exception: the old approval is not carried over.
    expected = "exception_approved" if approved_delta == 1000 else "mismatched"
    assert (status, delta) == (expected, 1000)
