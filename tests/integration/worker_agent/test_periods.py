"""Closing a period for real (UC-10-02): the snapshot as of the business date with its hashes,
the reconciliation on the captured rows, the period closed or close_failed."""

from __future__ import annotations

import decimal
import gzip
import hashlib
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import periods, resolutions, storage
from services.worker_agent.events import Event, OutboxTableSource
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


# --- filing (UC-10-03) ---------------------------------------------------------------------------


def closed(bank: Bank, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    pid, _ = close(bank, 8000, monkeypatch)
    return pid


def file(bank: Bank, pid: uuid.UUID, approval: uuid.UUID | None = None) -> None:
    """What :publish records: the report, its date, the filer, then period.publish_requested
    naming the approval of each manual number."""
    bank.seed.conn.cursor().execute(
        "UPDATE period.period_close SET filed_report_code = 'BC_NO_XAU', filed_at = '2026-10-10',"
        " updated_by = %s, updated_at = now() WHERE id = %s",
        (str(bank.user), str(pid)),
    )
    overrides = (
        [
            {
                "metric_version_id": str(npl_version(bank)),
                "reported_value": "31.5",
                "override_reason": "Số trên báo cáo đã nộp",
                "approval_id": str(approval),
            }
        ]
        if approval
        else []
    )
    bank.seed.row(
        "period.outbox",
        org_id=bank.org,
        aggregate_type="period_close",
        aggregate_id=pid,
        event_type=periods.PUBLISH,
        payload=json.dumps(
            {"period_close_id": str(pid), "data_space_id": str(bank.space), "overrides": overrides}
        ),
    )


def npl_version(bank: Bank) -> uuid.UUID:
    return bank.seed.one("SELECT id FROM context.metric_version WHERE metric_id = %s", bank.npl)


def override(bank: Bank, pid: uuid.UUID, status: str = "open") -> uuid.UUID:
    controller = bank.seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-ks", username="ks", display_name="ks"
    )
    return bank.seed.row(
        "inbox.hitl_request",
        org_id=bank.org,
        data_space_id=bank.space,
        kind="period_exception",
        risk="high",
        title="Duyệt số nộp khác số tính",
        source_service="period",
        source_ref=pid,
        assignee_id=controller,
        status=status,
        context=json.dumps(
            {
                "period_close_id": str(pid),
                "metric_version_id": str(npl_version(bank)),
                "reported_value": "31.5",
                "override_reason": "Số trên báo cáo đã nộp",
            }
        ),
    )


async def decide(bank: Bank, request: uuid.UUID, status: str, source_ref: uuid.UUID) -> None:
    controller = bank.seed.one(
        "SELECT assignee_id FROM inbox.hitl_request WHERE id = %s", request
    ) or bank.seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-kt", username="kt", display_name="kt"
    )
    bank.seed.conn.cursor().execute(
        "UPDATE inbox.hitl_request SET status = %s, resolved_by = %s, resolved_at = now()"
        " WHERE id = %s",
        (status, str(controller), str(request)),
    )
    await resolutions.act_on_decision(
        Event(
            event_id=uuid.uuid4(),
            org_id=bank.org,
            event_type=resolutions.EVENT_TYPE,
            aggregate_type="hitl_request",
            aggregate_id=request,
            payload={
                "hitl_request_id": str(request),
                "kind": "period_exception",
                "status": status,
                "source_service": "period",
                "source_ref": str(source_ref),
                "resolved_by": str(controller),
            },
        )
    )


@pytest.mark.req("AC-10-03-01")
async def test_filing_freezes_every_certified_number_of_the_snapshot(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = closed(bank, monkeypatch)
    await drain()
    file(bank, pid)
    await drain()
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "filed"
    rows = seed.all(
        "SELECT m.code, r.value, r.manual_override, r.report_ref, r.sha256"
        " FROM period.published_result r"
        " JOIN context.metric_version v ON v.id = r.metric_version_id"
        " JOIN context.metric m ON m.id = v.metric_id"
        " WHERE r.period_close_id = %s ORDER BY m.code",
        pid,
    )
    assert [(r[0], r[2], r[3]) for r in rows] == [
        ("DU_NO", False, "BC_NO_XAU"),
        ("NPL_RATIO", False, "BC_NO_XAU"),
    ]
    assert all(len(r[4]) == 64 for r in rows)
    # The 30/09 snapshot without NOI_BO: what the reconciliation's 8000 holds, less internal loans.
    du_no, npl = rows[0][1], rows[1][1]
    assert 0 < du_no < 8000 and 0 < npl < 100
    assert (
        seed.one(
            "SELECT count(*) FROM period.outbox WHERE aggregate_id = %s AND event_type = %s",
            pid,
            periods.PUBLISHED,
        )
        == 1
    )


@pytest.mark.req("AC-10-03-02")
async def test_a_manual_number_waits_for_its_controller(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = closed(bank, monkeypatch)
    await drain()
    request = override(bank, pid)
    file(bank, pid, request)
    await drain()
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "closed"
    assert (
        seed.one("SELECT count(*) FROM period.published_result WHERE period_close_id = %s", pid)
        == 0
    )
    await decide(bank, request, "approved", pid)
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "filed"
    reported, manual, reason, approver = seed.one(
        "SELECT reported_value, manual_override, override_reason, approved_by"
        " FROM period.published_result WHERE period_close_id = %s AND metric_version_id = %s",
        pid,
        npl_version(bank),
    )
    assert (reported, manual, reason) == (decimal.Decimal("31.5"), True, "Số trên báo cáo đã nộp")
    assert approver == seed.one("SELECT assignee_id FROM inbox.hitl_request WHERE id = %s", request)


async def test_a_refused_manual_number_leaves_the_period_to_file_again(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = closed(bank, monkeypatch)
    await drain()
    request = override(bank, pid)
    file(bank, pid, request)
    await drain()
    await decide(bank, request, "rejected", pid)
    status, report = seed.one(
        "SELECT status, filed_report_code FROM period.period_close WHERE id = %s", pid
    )
    assert (status, report) == ("closed", None)
    assert (
        seed.one("SELECT count(*) FROM period.published_result WHERE period_close_id = %s", pid)
        == 0
    )


async def test_a_snapshot_whose_tables_changed_is_not_filed(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = closed(bank, monkeypatch)
    await drain()
    # As if the source rows had moved since the close: the locked file no longer matches them.
    seed.conn.cursor().execute(
        "UPDATE period.period_snapshot_file SET sha256 = %s WHERE period_close_id = %s",
        ("b" * 64, str(pid)),
    )
    file(bank, pid)
    await drain()
    status, report = seed.one(
        "SELECT status, filed_report_code FROM period.period_close WHERE id = %s", pid
    )
    assert (status, report) == ("closed", None)


@pytest.mark.parametrize(
    ("decision", "after"), [("approved", "exception_approved"), ("rejected", "mismatched")]
)
async def test_an_exception_decided_in_the_inbox_moves_its_item(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    after: str,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid, _ = close(bank, 7000, monkeypatch)
    await drain()
    request = seed.row(
        "inbox.hitl_request",
        org_id=bank.org,
        data_space_id=bank.space,
        kind="period_exception",
        title="Ngoại lệ đối soát tong_du_no",
        source_service="period",
        assignee_capability="compliance",
    )
    item = seed.one(
        "SELECT id FROM period.period_reconciliation"
        " WHERE period_close_id = %s AND item_code = 'tong_du_no'",
        pid,
    )
    seed.conn.cursor().execute(
        "UPDATE period.period_reconciliation SET status = 'exception_pending', hitl_request_id = %s"
        " WHERE id = %s",
        (str(request), str(item)),
    )
    await decide(bank, request, decision, item)
    assert seed.one("SELECT status FROM period.period_reconciliation WHERE id = %s", item) == after


async def test_a_filing_waits_for_an_approval_not_opened_yet(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = closed(bank, monkeypatch)
    await drain()
    # The filing names its approval before the inbox has opened the request.
    file(bank, pid, uuid.uuid4())
    await drain()
    assert seed.one("SELECT status FROM period.period_close WHERE id = %s", pid) == "closed"
    assert (
        seed.one("SELECT count(*) FROM period.published_result WHERE period_close_id = %s", pid)
        == 0
    )
