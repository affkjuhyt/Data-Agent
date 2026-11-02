"""A metric version from preview to certified (metric_changes.py) on the bank of test_answers.py:
the preview opens a metric_change proposal with what the change reaches, an accepted proposal
certifies the version unless something it reaches would break, and a rejected one sends it back to
draft. The preview's figures are the new and the certified version by month over the six months
before 19/10/2026, next to the figure filed for September."""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import metric_changes, proposals
from services.worker_agent.answer.narrate import vn_number
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
    author: uuid.UUID | None = None,
    recorded: bool = True,
) -> uuid.UUID:
    version = bank.seed.row(
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
    if recorded:
        # As the api writes it: metric_version has no author column, its created event does.
        bank.seed.row(
            "context.outbox",
            org_id=bank.org,
            aggregate_type="metric_version",
            aggregate_id=version,
            event_type="metric_version.created",
            payload=json.dumps(
                {"version_id": str(version), "created_by": str(author or bank.user)}
            ),
        )
    return version


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
    # The review page's "Ảnh hưởng" table (frontend lib/v1/api.ts readImpact) is drawn from these.
    places = seed.one("SELECT decimals FROM context.metric_version WHERE id = %s", v2)
    assert impact["columns"] == ["v1", "v2"] and impact["window_label"] == "chạy thử trên 6 tháng"
    assert impact["rows"][-1] == {
        "period": "2026-09",
        "before": vn_number(decimal.Decimal(3000), places),
        "after": vn_number(decimal.Decimal(8000), places),
        "delta": "+" + vn_number(decimal.Decimal(5000), places),
    }
    assert impact["rows"][0] == {"period": "2026-04", "before": "—", "after": "—", "delta": "—"}
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


async def test_months_without_figures_move_the_comparison_to_the_last_months_with_data(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A warehouse that stops in the past (AdventureWorks ends in 2014): the six months before today
    # hold nothing, so the reviewer is shown the last months that do, and told so.
    monkeypatch.setattr(metric_changes, "_today", lambda: datetime.date(2027, 6, 15))
    bank = build(seed, warehouse, monkeypatch)
    metric, _ = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"])
    proposal = await preview(bank, v2)

    diff, impact = seed.one("SELECT diff, impact FROM inbox.proposal WHERE id = %s", proposal)
    months = [row["period"] for row in diff["preview"]]
    assert months[-1] == "2026-09" and 0 < len(months) <= 6, months
    assert all(row["new"] is not None for row in diff["preview"])
    assert diff["preview_note"] == (
        "Không có số trong 6 tháng trước 06/2027; so trên các tháng cuối có dữ liệu,"
        f" {months[0][5:]}/{months[0][:4]} đến 09/2026."
    )
    assert impact["window_label"] == diff["preview_note"]
    assert [row["period"] for row in impact["rows"]] == months


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
    # Back to draft, so its steward can fix the definition and propose it again.
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v2) == "draft"
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


async def test_its_author_cannot_approve_a_version_someone_else_previewed(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    author = reviewer(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"], author=author)
    proposal = await preview(bank, v2)  # asked by bank.user, not the author
    assert seed.one("SELECT proposed_by FROM inbox.proposal WHERE id = %s", proposal) == author
    await decide(bank, proposal, "accepted")  # decided by the author
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"


@pytest.mark.parametrize(("switch", "certified"), [(False, False), (True, True)])
async def test_a_metric_version_its_author_accepted_is_certified_only_when_the_org_allows_it(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    switch: bool,
    certified: bool,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    author = reviewer(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"], author=author)
    proposal = await preview(bank, v2)
    seed.conn.cursor().execute(
        "UPDATE org.organization SET allow_self_approval = %s WHERE id = %s",
        (switch, str(bank.org)),
    )
    await decide(bank, proposal, "accepted")  # decided by the author
    expected = "applied" if certified else "apply_failed"
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == expected
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v2) == (
        "certified" if certified else "draft"
    )


async def test_an_edited_proposal_is_not_certified_as_the_version(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"])
    proposal = await preview(bank, v2)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE inbox.proposal SET diff = diff || '{\"version\": 3}'::jsonb WHERE id = %s",
            (str(proposal),),
        )
    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"


async def test_figures_over_a_masked_table_are_withheld_not_broken(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, _ = du_no(bank)
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Che số dư",
        kind="mask",
        applies_to=json.dumps({}),
        columns=["balance"],
        mask_method="null",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    v2 = new_version(bank, metric, dims=["vung", "san_pham"])
    proposal = await preview(bank, v2)
    diff, impact = seed.one("SELECT diff, impact FROM inbox.proposal WHERE id = %s", proposal)
    assert "chính sách truy cập" in diff["preview_withheld"]
    assert all(row["new"] is None and row["current"] is None for row in diff["preview"])
    assert impact["breaks"] == []


async def test_a_version_without_a_recorded_author_is_not_certified(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail closed: the reviewer could be its author, so it waits to be saved again as a new version.
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"], recorded=False)
    proposal = await preview(bank, v2)
    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"


async def test_a_version_without_a_recorded_author_is_not_certified_even_when_the_org_allows_it(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    metric, v1 = du_no(bank)
    v2 = new_version(bank, metric, dims=["vung", "san_pham"], recorded=False)
    proposal = await preview(bank, v2)
    seed.conn.cursor().execute(
        "UPDATE org.organization SET allow_self_approval = true WHERE id = %s", (str(bank.org),)
    )
    await decide(bank, proposal, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", proposal) == "apply_failed"
    assert seed.one("SELECT status FROM context.metric_version WHERE id = %s", v1) == "certified"
