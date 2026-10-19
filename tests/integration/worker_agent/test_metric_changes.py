"""A metric version from preview to certified (metric_changes.py) on the bank of test_answers.py:
the preview opens a metric_change proposal with what the change reaches, an accepted proposal
certifies the version unless something it reaches would break, and a rejected one sends it back to
draft. The preview's figures are the new and the certified version by month over the six months
before 19/10/2026, next to the figure filed for September."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import metric_changes, proposals
from services.worker_agent.events import Event

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def du_no(bank: Bank) -> tuple[uuid.UUID, uuid.UUID]:
    return bank.seed.one(
        "SELECT m.id, v.id FROM context.metric m JOIN context.metric_version v ON v.metric_id = m.id"
        " WHERE m.org_id = %s AND m.code = 'DU_NO'",
        bank.org,
    )


def dimension(bank: Bank, code: str) -> uuid.UUID:
    return bank.seed.one(
        "SELECT id FROM context.semantic_dimension WHERE org_id = %s AND code = %s", bank.org, code
    )


def new_version(
    bank: Bank,
    metric: uuid.UUID,
    *,
    dims: list[str],
    unit: str = "tỷ đồng",
    expression: str = "du_no",
) -> uuid.UUID:
    return bank.seed.row(
        "context.metric_version",
        org_id=bank.org,
        metric_id=metric,
        version=2,
        display_name="Dư nợ",
        expression=expression,
        unit=unit,
        period_semantics="end_of_period",
        status="testing",
        allowed_dimensions=[dimension(bank, d) for d in dims],
        filters=json.dumps([]),
    )


def file_september(bank: Bank, version: uuid.UUID, value: int) -> None:
    close = bank.seed.row(
        "period.period_close",
        org_id=bank.org,
        data_space_id=bank.space,
        period_label="2026-09",
        business_date="2026-09-30",
        status="filed",
        snapshot_hash="ab" * 32,
        closed_at="2026-10-05T00:00:00Z",
    )
    bank.seed.row(
        "period.published_result",
        org_id=bank.org,
        period_close_id=close,
        metric_version_id=version,
        value=value,
        report_ref="NHNN-T9",
        sha256="cd" * 32,
    )


@pytest.fixture(autouse=True)
def october(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metric_changes, "_today", lambda: datetime.date(2026, 10, 19))


def watch_by(bank: Bank, metric: uuid.UUID, split: str) -> uuid.UUID:
    return bank.seed.row(
        "monitor.monitor",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        metric_id=metric,
        scope_filter=json.dumps({"split": split}),
        rule_kind="threshold",
        rule=json.dumps({"direction": "above", "value": 3000}),
        schedule_cron="0 7 * * *",
    )


async def preview(bank: Bank, version: uuid.UUID) -> uuid.UUID:
    await metric_changes.preview_requested(
        Event(
            uuid.uuid4(),
            bank.org,
            metric_changes.PREVIEW,
            "metric_version",
            version,
            {"version_id": str(version), "requested_by": str(bank.user)},
        )
    )
    return bank.seed.one("SELECT proposal_id FROM context.metric_version WHERE id = %s", version)


def reviewer(bank: Bank) -> uuid.UUID:
    return bank.seed.one(
        "SELECT id FROM org.app_user WHERE org_id = %s AND username = 'o'", bank.org
    )


async def decide(bank: Bank, proposal: uuid.UUID, status: str) -> None:
    # The api records the decision on the proposal before it emits proposal.decided.
    with bank.seed.conn.cursor() as cur:
        cur.execute("UPDATE inbox.proposal SET status = %s WHERE id = %s", (status, str(proposal)))
    await proposals.decided(
        Event(
            uuid.uuid4(),
            bank.org,
            proposals.DECIDED,
            "proposal",
            proposal,
            {"proposal_id": str(proposal), "status": status, "reviewer_id": str(reviewer(bank))},
        )
    )


async def test_a_change_without_breaks_is_proposed_with_its_reach_and_certified_once_accepted(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    watch_by(bank, metric, "vung")
    file_september(bank, v1, 3000)
    # Version 2 drops version 1's filter on internal lending (5000 in September).
    v2 = new_version(bank, metric, dims=["vung", "san_pham"])
    proposal = await preview(bank, v2)

    kind, diff, impact, level = seed.one(
        "SELECT kind, diff, impact, impact_level FROM inbox.proposal WHERE id = %s", proposal
    )
    assert (kind, level) == ("metric_change", "medium")
    assert impact["counts"]["monitors"] == 1 and impact["breaks"] == []
    assert diff["version"] == 2 and diff["replaces_version"] == 1
    assert "filters" in diff["changes"] and "expression" not in diff["changes"]
    months = {row["period"]: row for row in diff["preview"]}
    assert list(months) == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    september = {k: float(v) for k, v in months["2026-09"].items() if k != "period" and v}
    assert september == {
        "new": 8000,
        "current": 3000,
        "filed": 3000,
        "change_vs_current": 5000,
        "change_vs_filed": 5000,
    }
    assert float(months["2026-08"]["new"]) == float(months["2026-08"]["current"]) == 500
    assert months["2026-04"]["new"] is None
    assert impact["counts"]["filed_mismatches"] == 1
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v2) == (
        "pending_review"
    )

    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "applied"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v2) == "certified"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "superseded"
    assert seed.one("SELECT current_version FROM context.metric WHERE id = %s", metric) == 2
    assert (
        seed.one(
            "SELECT count(*) FROM context.outbox WHERE aggregate_id = %s"
            " AND event_type = 'metric_version.certified'",
            v2,
        )
        == 1
    )


async def test_a_change_that_breaks_a_monitor_is_flagged_and_not_applied(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    watch_by(bank, metric, "vung")
    # The new version no longer allows "vung", which the monitor is cut by, and changes the unit.
    v2 = new_version(bank, metric, dims=["san_pham"], unit="triệu đồng")
    proposal = await preview(bank, v2)

    impact, level, diff = seed.one(
        "SELECT impact, impact_level, diff FROM inbox.proposal WHERE id = %s", proposal
    )
    assert level == "high"
    texts = [b["text"] for b in impact["breaks"]]
    assert any("vung" in t for t in texts) and any("triệu đồng" in t for t in texts)
    assert diff["changes"]["dimensions_removed"] == ["vung"]

    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v2) == (
        "pending_review"
    )
    assert seed.one("SELECT current_version FROM context.metric WHERE id = %s", metric) == 1


async def test_a_rejected_change_sends_the_version_back_to_draft(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, _ = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"])
    proposal = await preview(bank, v2)
    await decide(bank, proposal, "rejected")
    assert seed.one("SELECT status, proposal_id FROM context.metric_version WHERE id = %s", v2) == (
        "draft",
        None,
    )


async def test_a_definition_that_does_not_compile_is_a_break_and_never_certified(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung"], expression="khong_co_thuoc_do")
    proposal = await preview(bank, v2)
    impact, level = seed.one(
        "SELECT impact, impact_level FROM inbox.proposal WHERE id = %s", proposal
    )
    assert level == "high"
    assert [b["kind"] for b in impact["breaks"]] == ["definition"]
    assert "khong_co_thuoc_do" in impact["breaks"][0]["text"]

    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"
