"""UC-02-07: worker-agent drafts a session report from the tiles' latest snapshots and runs its four
checks (services/worker_agent/session_reports.py) on the bank schema of the answer tests. A check
that cannot be judged fails; the report is ready only when all four pass."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import session_reports, storage, tiles
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .test_answers import Bank, build
from .test_column_masking import catalogue_columns

warehouse = test_answers.warehouse

pytestmark = [pytest.mark.integration]

HANDLERS = {**tiles.HANDLERS, **session_reports.HANDLERS}
SQL = "SELECT branch_code, sum(balance) AS b FROM loan GROUP BY 1 ORDER BY 1"


class Store:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        self.files[(bucket, key)] = body


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    fake = Store()
    monkeypatch.setattr(storage, "_store", fake)
    return fake


def _emit(bank: Bank, event_type: str, aggregate: uuid.UUID, **payload: Any) -> None:
    bank.seed.row(
        "workspace.outbox",
        org_id=bank.org,
        aggregate_type="session_report",
        aggregate_id=aggregate,
        event_type=event_type,
        payload=json.dumps(payload, default=str),
    )


async def _drain() -> None:
    source = OutboxTableSource(schema="workspace", event_types=frozenset(HANDLERS))
    while await run_once(source, HANDLERS, batch=50):
        pass


def _workspace(bank: Bank, sql: str = SQL) -> tuple[uuid.UUID, uuid.UUID]:
    ws = bank.seed.row(
        "workspace.workspace",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        title="Dư nợ T9",
    )
    tile = bank.seed.row(
        "workspace.tile",
        org_id=bank.org,
        workspace_id=ws,
        position=0,
        kind="sql",
        title="Dư nợ theo chi nhánh",
        spec=json.dumps({"sql": sql}),
    )
    return ws, tile


async def _refresh(bank: Bank, ws: uuid.UUID, tile: uuid.UUID) -> None:
    bank.seed.row(
        "workspace.outbox",
        org_id=bank.org,
        aggregate_type="workspace",
        aggregate_id=ws,
        event_type=tiles.REFRESH,
        payload=json.dumps(
            {
                "workspace_id": str(ws),
                "requested_by": str(bank.user),
                "tile_ids": [str(tile)],
                "as_of": None,
            }
        ),
    )
    await _drain()


async def _draft(bank: Bank, ws: uuid.UUID, tiles_: list[uuid.UUID]) -> uuid.UUID:
    report = uuid.uuid4()
    bank.seed.row(
        "workspace.session_report",
        id=report,
        org_id=bank.org,
        workspace_id=ws,
        version=1,
        title="Báo cáo dư nợ T9",
        body_ref=f"session-reports/{ws}/{report}/v1.md",
        created_by=bank.user,
        updated_by=bank.user,
    )
    _emit(
        bank,
        session_reports.CREATED,
        report,
        session_report_id=report,
        workspace_id=ws,
        data_space_id=bank.space,
        version=1,
        tile_ids=[str(t) for t in tiles_],
        requested_by=bank.user,
    )
    await _drain()
    return report


async def _check(bank: Bank, report: uuid.UUID) -> tuple[str, dict[str, Any]]:
    with bank.seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE workspace.session_report SET status = 'checking', checks = '{}' WHERE id = %s",
            (str(report),),
        )
    _emit(
        bank,
        session_reports.STATUS_CHANGED,
        report,
        session_report_id=report,
        status="checking",
        requested_by=bank.user,
    )
    await _drain()
    status, checks = bank.seed.one(  # type: ignore[misc]
        "SELECT status, checks FROM workspace.session_report WHERE id = %s", report
    )
    return status, checks


async def test_a_report_is_drafted_from_the_tiles_and_passes_its_four_checks(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    ws, tile = _workspace(bank)
    await _refresh(bank, ws, tile)
    report = await _draft(bank, ws, [tile])

    ((bucket, key), body) = next(iter(store.files.items()))
    assert bucket == "da-results" and key == f"session-reports/{ws}/{report}/v1.md"
    markdown = body.decode()
    assert markdown.startswith("# Báo cáo dư nợ T9\n") and "## Số liệu" in markdown
    figures = [line for line in markdown.splitlines() if line.endswith("| grounded")]
    assert len(figures) == 3 and figures[0].startswith("- Dư nợ theo chi nhánh: branch_code = ")
    assert (
        seed.one(
            "SELECT via FROM pipeline.lineage_edge WHERE to_kind = 'report' AND to_id = %s", report
        )
        == "snapshot"
    )

    status, checks = await _check(bank, report)
    assert status == "ready", checks
    assert {name: c["status"] for name, c in checks.items()} == {
        "grounded": "pass",
        "filed": "pass",
        "rights": "pass",
        "personal_data": "pass",
    }
    assert checks["grounded"]["title"] == "Số có nguồn"
    # The worker tells the move, as the api does for its own.
    assert (
        seed.one(
            "SELECT payload->>'status' FROM workspace.outbox WHERE aggregate_id = %s"
            " AND event_type = %s ORDER BY created_at DESC LIMIT 1",
            report,
            session_reports.STATUS_CHANGED,
        )
        == "ready"
    )


async def test_a_tile_never_run_is_ungrounded_and_nothing_else_passes_without_figures(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ws, tile = _workspace(bank)
    report = await _draft(bank, ws, [tile])
    (body,) = store.files.values()
    assert "- Dư nợ theo chi nhánh: chưa có số | ungrounded" in body.decode()
    status, checks = await _check(bank, report)
    assert status == "failed_checks"
    assert checks["grounded"]["status"] == "fail"
    assert "không có số đã chạy" in checks["grounded"]["detail"]


async def test_a_report_never_drafted_fails_every_check(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ws, _ = _workspace(bank)
    report = await _draft(bank, ws, [])
    status, checks = await _check(bank, report)
    assert status == "failed_checks"
    assert all(c["status"] == "fail" for c in checks.values()), checks


async def test_a_reader_with_a_narrower_policy_fails_the_rights_check(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    ws, tile = _workspace(bank)
    reader = seed.one("SELECT id FROM org.app_user WHERE org_id = %s AND username = 'o'", bank.org)
    seed.row(
        "workspace.workspace_share",
        org_id=bank.org,
        workspace_id=ws,
        principal_type="user",
        principal_id=reader,
        permission="view",
        granted_by=bank.user,
    )
    # The reader sees only retail loans: the report's totals would be more than theirs.
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Chỉ bán lẻ cho O",
        kind="row",
        applies_to=json.dumps({"user_ids": [str(reader)]}),
        condition_expr="group_code = 1",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    await _refresh(bank, ws, tile)
    report = await _draft(bank, ws, [tile])
    status, checks = await _check(bank, report)
    assert status == "failed_checks"
    assert checks["rights"]["status"] == "fail"
    assert "o có phạm vi hoặc chính sách khác" in checks["rights"]["detail"]
    assert checks["grounded"]["status"] == "pass"


@pytest.mark.parametrize(
    "sql",
    [
        SQL,
        # Renamed, inside a function, or only filtered on: the output names hide it, the SQL not.
        "SELECT upper(branch_code) AS x, sum(balance) AS b FROM loan GROUP BY 1",
        "SELECT sum(balance) AS b FROM loan WHERE branch_code <> ''",
        "SELECT * FROM loan",
    ],
)
async def test_a_figure_from_a_personal_data_column_fails(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store, sql: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.column_meta SET pii_label = 'account_no'"
            " WHERE dataset_id = %s AND name = 'branch_code'",
            (str(bank.loans),),
        )
    ws, tile = _workspace(bank, sql)
    await _refresh(bank, ws, tile)
    report = await _draft(bank, ws, [tile])
    status, checks = await _check(bank, report)
    assert status == "failed_checks"
    assert checks["personal_data"]["status"] == "fail"
    assert "branch_code (account_no)" in checks["personal_data"]["detail"], checks


async def test_a_live_figure_of_a_filed_period_fails(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    ws, tile = _workspace(bank)
    await _refresh(bank, ws, tile)
    version = seed.one(
        "SELECT v.id FROM context.metric_version v JOIN context.metric m ON m.id = v.metric_id"
        " WHERE m.org_id = %s LIMIT 1",
        bank.org,
    )
    close = seed.row(
        "period.period_close",
        org_id=bank.org,
        data_space_id=bank.space,
        period_label="2026-09",
        business_date="2026-09-30",
        status="filed",
        snapshot_hash="ab" * 32,
        closed_at="2026-10-05T00:00:00Z",
    )
    seed.row(
        "period.published_result",
        org_id=bank.org,
        period_close_id=close,
        metric_version_id=version,
        value=3000,
        report_ref="NHNN-T9",
        sha256="cd" * 32,
    )
    # The tile's run computed that metric for 30/09 live, not from the filed snapshot.
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE query.query_run SET metric_version_ids = ARRAY[%s]::uuid[], as_of = '2026-09-30'"
            " WHERE caller_id = %s",
            (str(version), str(tile)),
        )
    report = await _draft(bank, ws, [tile])
    status, checks = await _check(bank, report)
    assert status == "failed_checks"
    assert checks["filed"]["status"] == "fail"
    assert "kỳ 2026-09-30 đã nộp" in checks["filed"]["detail"]
