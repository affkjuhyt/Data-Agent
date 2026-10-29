"""Crawl sources and agent schedule triggers on Temporal (TM-03): what schedules.targets.wanted
asks a Schedule for, and what one firing of it does."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent.schedules import targets

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]

# 08:00 in Hà Nội on Thursday 22/10/2026 is 01:00 UTC.
AT = datetime.datetime(2026, 10, 22, 1, 0, tzinfo=datetime.UTC)
CHECKLIST = {k: {"result": "pass"} for k in ("terms", "robots", "copyright", "personal_data")}
SHA = "0" * 64
# A field list the active-version CHECK accepts (acquire.value_constraints_ok), as the crawler's
# own tests write it.
FIELDS = [
    {"name": "bank", "type": "text", "required": True, "key": True, "locator": "td.bank"},
    {
        "name": "rate_12m",
        "type": "percent",
        "required": True,
        "locator": "td.r12",
        "value_constraints": {"min": 0, "max": 15, "unit": "%/năm"},
    },
]


def execute(seed: Seed, sql: str, *params: object) -> None:
    with seed.conn.cursor() as cur:
        cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def crawl_source(
    bank: Bank, cron: str, *, review_due: datetime.datetime | None = None
) -> uuid.UUID:
    seed = bank.seed
    due = review_due or AT + datetime.timedelta(days=90)
    source = seed.row(
        "acquire.crawl_source",
        org_id=bank.org,
        data_space_id=bank.space,
        name=f"Nguồn {uuid.uuid4().hex[:6]}",
        goal="Lãi suất 12 tháng",
        kind="web_page",
        seed_urls=Json(["https://www.example.vn/lai-suat"]),
        allowed_domains=["www.example.vn"],
        trust_level=0.9,
        owner_id=bank.user,
        review_gate="all",
        terms_url="https://www.example.vn/dieu-khoan",
        legal_status="approved",
        legal_checklist=Json(CHECKLIST),
        legal_evidence=Json(
            [
                {
                    "kind": "terms",
                    "object_key": "legal/t.html",
                    "sha256": SHA,
                    "captured_at": "2026-10-01",
                }
            ]
        ),
        # The reviewer is never the owner (crawl_source CHECK, BR-174).
        legal_reviewed_by=uuid.uuid4(),
        legal_reviewed_at=due - datetime.timedelta(days=91),
        legal_review_due_at=due,
        terms_sha256=SHA,
        schedule_cron=cron,
    )
    version = seed.row(
        "acquire.crawl_schema_version",
        org_id=bank.org,
        source_id=source,
        version=1,
        status="active",
        record_selector="table tr",
        fields=Json(FIELDS),
        approved_by=bank.user,
        approved_at=AT - datetime.timedelta(days=2),
        test_required_accuracy=0.97,
    )
    execute(
        seed,
        "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s"
        " WHERE id = %s",
        version,
        source,
    )
    return source


async def test_a_crawl_source_on_a_schedule_queues_a_run_its_crawler_claims(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    source = crawl_source(bank, "0 8 * * *")
    wanted = {t.schedule_id: t for t in await targets.wanted(bank.org)}
    assert wanted[f"crawl:{bank.org}:{source}"].zone == "Asia/Ho_Chi_Minh"

    assert (await targets.fire("crawl", bank.org, source, AT)).startswith("crawl_run ")
    # The first run is still queued: a second firing does not stack another.
    assert await targets.fire("crawl", bank.org, source, AT) == "a run is still going"
    trigger, status = seed.one(
        "SELECT trigger, status FROM acquire.crawl_run WHERE source_id = %s", source
    )  # type: ignore[misc]
    assert (trigger, status) == ("schedule", "queued")
    payload = seed.one(
        "SELECT payload FROM acquire.outbox WHERE event_type = 'acquire.run.queued'"
        " AND payload->>'source_id' = %s",
        str(source),
    )
    assert payload["trigger"] == "schedule"  # type: ignore[index]


async def test_a_crawl_source_whose_legal_review_lapsed_is_not_run(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    # Active while its review is in force (the database refuses anything else); the review then
    # lapses with time, and a firing after that day queues nothing.
    source = crawl_source(bank, "0 8 * * *", review_due=AT + datetime.timedelta(days=30))
    lapsed = AT + datetime.timedelta(days=31)
    assert await targets.fire("crawl", bank.org, source, lapsed) == "legal review not in force"
    assert seed.one("SELECT count(*) FROM acquire.crawl_run WHERE source_id = %s", source) == 0


def agent_with_trigger(bank: Bank, cron: str, **trigger: Any) -> tuple[uuid.UUID, uuid.UUID]:
    seed = bank.seed
    agent_id = seed.row(
        "agent.agent_def",
        org_id=bank.org,
        data_space_id=bank.space,
        code=f"a-{uuid.uuid4().hex[:6]}",
        name="Theo dõi nợ xấu",
        kind="analysis",
        owner_user_id=bank.user,
        account_user_id=bank.user,
        current_config_version=1,
        status="running",
    )
    seed.row(
        "agent.agent_config",
        org_id=bank.org,
        agent_id=agent_id,
        version=1,
        goal="Tỷ lệ nợ xấu theo vùng",
        autonomy="suggest_only",
        checkpoints=json.dumps([]),
        limits=json.dumps({}),
        data_scope=json.dumps({}),
    )
    trigger_id = seed.row(
        "agent.agent_trigger",
        org_id=bank.org,
        agent_id=agent_id,
        kind="schedule",
        cron=cron,
        **trigger,
    )
    return agent_id, trigger_id


async def test_a_schedule_trigger_starts_a_run_as_the_agents_own_account(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    agent_id, trigger_id = agent_with_trigger(bank, "0 1 * * *")
    wanted = {t.schedule_id: t for t in await targets.wanted(bank.org)}
    assert wanted[f"agent:{bank.org}:{trigger_id}"].zone == "UTC"

    assert (await targets.fire("agent", bank.org, trigger_id, AT)).startswith("agent run ")
    # A firing dedupes on created_at >= its minute; the database clock is today, so the run is
    # dated back to the minute it served, as it would be when Temporal fires on time.
    execute(
        seed,
        "UPDATE agent.agent_run SET created_at = %s WHERE agent_id = %s",
        AT.isoformat(),
        agent_id,
    )
    assert await targets.fire("agent", bank.org, trigger_id, AT) == "already served"
    kind, ref, initiated_by, is_test, version = seed.one(
        "SELECT trigger_kind, trigger_ref, initiated_by, is_test, config_version"
        " FROM agent.agent_run WHERE agent_id = %s",
        agent_id,
    )  # type: ignore[misc]
    assert (kind, ref, initiated_by, is_test, version) == (
        "schedule",
        str(trigger_id),
        None,
        False,
        1,
    )
    # Next day, the first run never ended: no_overlap (the default) holds the second.
    tomorrow = AT + datetime.timedelta(days=1)
    assert await targets.fire("agent", bank.org, trigger_id, tomorrow) == "a run is still going"
    # A paused agent's trigger has no Schedule and fires into nothing.
    execute(seed, "UPDATE agent.agent_def SET status = 'paused' WHERE id = %s", agent_id)
    assert f"agent:{bank.org}:{trigger_id}" not in {
        t.schedule_id for t in await targets.wanted(bank.org)
    }
    assert await targets.fire("agent", bank.org, trigger_id, tomorrow) == "agent not running"


async def test_a_trigger_keeps_its_min_interval(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    agent_id, trigger_id = agent_with_trigger(
        bank, "*/15 * * * *", no_overlap=False, min_interval="1 hour"
    )
    assert (await targets.fire("agent", bank.org, trigger_id, AT)).startswith("agent run ")
    later = AT + datetime.timedelta(minutes=15)
    # Dated back to the minute it served (the database clock is today), as in the test above.
    execute(
        seed,
        "UPDATE agent.agent_run SET created_at = %s WHERE agent_id = %s",
        AT.isoformat(),
        agent_id,
    )
    assert await targets.fire("agent", bank.org, trigger_id, later) == "within min_interval"
    assert seed.one("SELECT count(*) FROM agent.agent_run WHERE agent_id = %s", agent_id) == 1
