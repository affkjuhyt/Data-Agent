"""UC-08-03: worker-agent renders a scheduled report once per permission group, checks it before
sending, stores it and tells its recipients in the app (services/worker_agent/report_runs.py),
and the scheduler requests a run when the schedule's cron fires (scheduler._reports)."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import report_runs, scheduler, storage
from services.worker_agent.channels import gateway
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, activate, build
from .test_column_masking import catalogue_columns

warehouse = test_answers.warehouse

pytestmark = [pytest.mark.integration]

SQL = "SELECT branch_code, sum(balance) AS b FROM loan GROUP BY 1 ORDER BY 1"


class Store:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        assert bucket == "da-results"
        self.files[key] = body


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    fake = Store()
    monkeypatch.setattr(storage, "_store", fake)
    return fake


async def drain() -> None:
    source = OutboxTableSource(schema="monitor", event_types=frozenset(report_runs.HANDLERS))
    while await run_once(source, report_runs.HANDLERS, batch=20):
        pass


def schedule(
    bank: Bank, *, kind: str = "report", formats: tuple[str, ...] = ("html", "xlsx")
) -> uuid.UUID:
    ws = None
    if kind == "report":
        ws = bank.seed.row(
            "workspace.workspace",
            org_id=bank.org,
            data_space_id=bank.space,
            owner_id=bank.user,
            title="Dư nợ",
        )
        bank.seed.row(
            "workspace.tile",
            org_id=bank.org,
            workspace_id=ws,
            position=0,
            kind="sql",
            title="Dư nợ theo chi nhánh",
            spec=json.dumps({"sql": SQL}),
        )
    return bank.seed.row(
        "monitor.report_schedule",
        org_id=bank.org,
        data_space_id=bank.space,
        kind=kind,
        workspace_id=ws,
        owner_id=bank.user,
        title="Báo cáo dư nợ",
        cron="0 8 * * *",
        as_of_rule="end_of_previous_day",
        formats=list(formats),
        status="active",
    )


def recipient(
    bank: Bank,
    report: uuid.UUID,
    user: Any,
    group: str = "g1",
    channels: tuple[str, ...] = ("in_app",),
) -> None:
    bank.seed.row(
        "monitor.report_recipient",
        org_id=bank.org,
        report_schedule_id=report,
        principal_type="user",
        principal_id=user,
        channels=list(channels),
        permission_group=group,
    )


def request(bank: Bank, report: uuid.UUID, kind: str = "report") -> uuid.UUID:
    run = bank.seed.row(
        "monitor.report_run",
        org_id=bank.org,
        report_schedule_id=report,
        kind=kind,
        as_of="2026-10-21",
        trigger="run_now",
        status="running",
    )
    bank.seed.row(
        "monitor.outbox",
        org_id=bank.org,
        aggregate_type="report_run",
        aggregate_id=run,
        event_type=report_runs.EVENT_TYPE,
        payload=json.dumps({"report_run_id": str(run), "requested_by": str(bank.user)}),
    )
    return run


def run_of(seed: Seed, run: uuid.UUID) -> tuple[Any, ...]:
    return seed.one("SELECT status, checks, outputs FROM monitor.report_run WHERE id = %s", run)  # type: ignore[return-value]


def told(seed: Seed, run: uuid.UUID) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, kind, body FROM notify.notification WHERE source_ref = %s ORDER BY user_id",
            (str(run),),
        )
        return list(cur.fetchall())


def other_user(seed: Seed, bank: Bank) -> Any:
    return seed.one("SELECT id FROM org.app_user WHERE org_id = %s AND username = 'o'", bank.org)


async def test_a_report_is_rendered_stored_and_told_to_its_recipients(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    report = schedule(bank)
    recipient(bank, report, bank.user)
    recipient(bank, report, other_user(seed, bank))
    run = request(bank, report)
    await drain()
    status, checks, outputs = run_of(seed, run)
    assert status == "sent", (checks, outputs)
    assert {c["status"] for c in checks.values()} == {"pass"}
    assert sorted(o["format"] for o in outputs) == ["html", "xlsx"]
    assert all(o["delivery"] == "stored" and o["permission_group"] == "g1" for o in outputs)
    page = store.files[f"report-runs/{run}/g1.html"].decode()
    assert "Dư nợ theo chi nhánh" in page and "<td>HN1</td>" in page and "21/10/2026" in page
    # Both told in the app, with no figures in the notification.
    assert [(k, b) for _, k, b in told(seed, run)] == [("report", None), ("report", None)]
    assert (
        seed.one(
            "SELECT payload->>'status' FROM monitor.outbox WHERE aggregate_id = %s AND event_type = %s",
            run,
            report_runs.DELIVERED,
        )
        == "sent"
    )


async def test_a_member_with_narrower_rights_does_not_receive_the_groups_rendering(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    narrow = other_user(seed, bank)
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Chỉ bán lẻ cho O",
        kind="row",
        applies_to=json.dumps({"user_ids": [str(narrow)]}),
        condition_expr="group_code = 1",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    report = schedule(bank, formats=("html",))
    recipient(bank, report, bank.user)
    recipient(bank, report, narrow)  # saved in the same group before the policy
    run = request(bank, report)
    await drain()
    status, checks, _ = run_of(seed, run)
    assert status == "partially_sent" and checks["rights"]["status"] == "fail"
    notified = told(seed, run)
    # The group is rendered with one member's rights; the other, whose SQL would differ, is left
    # out. Either way no one receives figures computed with rights other than their own.
    assert len([u for u, k, _ in notified if k == "report"]) == 1
    # The owner hears who was left out, as an alert.
    assert [k for u, k, _ in notified if str(u) == str(bank.user)].count("alert") == 1


async def test_a_report_reading_personal_data_is_not_sent(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.column_meta SET pii_label = 'account_no'"
            " WHERE dataset_id = %s AND name = 'branch_code'",
            (str(bank.loans),),
        )
    report = schedule(bank)
    recipient(bank, report, bank.user)
    run = request(bank, report)
    await drain()
    status, checks, outputs = run_of(seed, run)
    assert (status, outputs) == ("blocked_by_checks", [])
    assert "branch_code (account_no)" in checks["personal_data"]["detail"]
    assert store.files == {}


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send(self, address: str, m: Any) -> None:
        self.sent.append((address, m))


async def test_a_bound_email_gets_the_card_and_an_unbound_one_makes_it_partial(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    mailbox = Mailbox()
    monkeypatch.setattr(gateway, "senders", lambda: {"email": mailbox})
    seed.row(
        "notify.channel_binding",
        org_id=bank.org,
        user_id=bank.user,
        channel="email",
        external_address="a@bank.example",
        verified_at="2026-10-17T00:00:00Z",
        status="active",
    )
    report = schedule(bank, formats=("html", "pdf"))
    recipient(bank, report, bank.user, channels=("in_app", "email"))
    recipient(bank, report, other_user(seed, bank), channels=("email",))  # bound to nothing
    run = request(bank, report)
    await drain()
    status, _, outputs = run_of(seed, run)
    assert status == "partially_sent"
    assert {o["format"]: o["delivery"] for o in outputs} == {
        "html": "stored",
        "pdf": "not_supported",
    }
    ((address, card),) = mailbox.sent
    assert address == "a@bank.example" and card.subject == "[Báo cáo] Báo cáo dư nợ"
    # A title, a date and the way in: no figure goes out by mail (BR-35).
    assert f"/reports/runs/{run}" in card.link and "HN1" not in card.text
    assert seed.one(
        "SELECT channel, status FROM notify.channel_delivery WHERE user_id = %s", bank.user
    ) == ("email", "sent")


async def test_a_digest_tells_its_owner_the_key_metrics_the_alerts_and_what_waits(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank, metrics=("DU_NO",))
    du_no = seed.one(
        "SELECT id FROM context.metric WHERE data_space_id = %s AND code = 'DU_NO'", bank.space
    )
    digest = schedule(bank, kind="digest", formats=("html",))
    recipient(bank, digest, bank.user)
    # The metric list travels only in the schedule's event (no column holds it).
    seed.row(
        "monitor.outbox",
        org_id=bank.org,
        aggregate_type="report_schedule",
        aggregate_id=digest,
        event_type="report_schedule.created",
        payload=json.dumps({"metric_ids": [str(du_no)]}),
        published_at="2026-10-01T00:00:00Z",
    )
    watch = seed.row(
        "monitor.monitor",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        metric_id=du_no,
        rule_kind="threshold",
        rule=json.dumps({"op": ">", "value": 1}),
        schedule_cron="0 8 * * *",
    )
    seed.row(
        "monitor.alert",
        org_id=bank.org,
        monitor_id=watch,
        fired_at="2026-09-29T02:00:00Z",
        summary="Dư nợ vượt ngưỡng",
        dedup_key="du-no-2026-09",
    )
    seed.row(
        "inbox.hitl_request",
        org_id=bank.org,
        kind="approve_proposal",
        title="Duyệt chỉ số",
        source_service="context",
        assignee_id=bank.user,
    )
    run = bank.seed.row(
        "monitor.report_run",
        org_id=bank.org,
        report_schedule_id=digest,
        kind="digest",
        as_of="2026-09-30",
        trigger="schedule",
        status="running",
    )
    seed.row(
        "monitor.outbox",
        org_id=bank.org,
        aggregate_type="report_run",
        aggregate_id=run,
        event_type=report_runs.EVENT_TYPE,
        payload=json.dumps({"report_run_id": str(run)}),
    )
    await drain()
    status, checks, outputs = run_of(seed, run)
    assert status == "sent", (checks, outputs)
    page = store.files[f"report-runs/{run}/g1.html"].decode()
    assert "Số liệu đến ngày 30/09/2026, 1 chỉ số chính." in page
    assert "Có 1 cảnh báo trong 7 ngày qua." in page and "Dư nợ vượt ngưỡng" in page
    assert "1 việc đang chờ bạn ở Cần bạn." in page
    # Every figure links to the run it was computed by.
    query_run = seed.one(
        "SELECT id FROM query.query_run WHERE caller_id = (SELECT v.id FROM context.metric_version v"
        " WHERE v.metric_id = %s ORDER BY v.version DESC LIMIT 1) ORDER BY started_at DESC LIMIT 1",
        du_no,
    )
    assert f"/query-runs/{query_run}" in page
    assert [(k, b) for _, k, b in told(seed, run)] == [("digest", None)]


@pytest.mark.req("AC-08-04-05")
async def test_a_quiet_week_still_makes_a_short_digest(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    digest = schedule(bank, kind="digest", formats=("html",))
    recipient(bank, digest, bank.user)
    run = request(bank, digest, kind="digest")
    await drain()
    status, _, _ = run_of(seed, run)
    assert status == "sent"
    assert (
        "Không có thay đổi vượt ngưỡng trong kỳ."
        in store.files[f"report-runs/{run}/g1.html"].decode()
    )


async def test_the_scheduler_requests_a_run_once_when_the_cron_fires(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    report = schedule(bank)
    # 08:00 in Hà Nội on 22/10 is 01:00 UTC; the report is dated the day before.
    at = datetime.datetime(2026, 10, 22, 1, 0, tzinfo=datetime.UTC)
    assert await scheduler._reports(bank.org, at) == 1
    assert await scheduler._reports(bank.org, at) == 0
    assert await scheduler._reports(bank.org, at + datetime.timedelta(hours=1)) == 0
    as_of, trigger, status = seed.one(
        "SELECT as_of, trigger, status FROM monitor.report_run WHERE report_schedule_id = %s",
        report,
    )  # type: ignore[misc]
    assert (str(as_of), trigger, status) == ("2026-10-21", "schedule", "running")
    assert (
        seed.one(
            "SELECT count(*) FROM monitor.outbox WHERE event_type = %s AND payload->>'report_schedule_id' = %s",
            report_runs.EVENT_TYPE,
            report,
        )
        == 1
    )
