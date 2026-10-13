"""POST /v1/crawl-sources:suggest (UC-14-01, added 12/10/2026) against PostgreSQL.

Proposing sources for a goal is the Agent Thu thập run's job: the api reserves the run id, writes
the request to acquire.outbox and answers 202 with a Location to the run. It registers nothing.
Organisation and users come from the acquire suite's fixture.
"""

from __future__ import annotations

import pytest

from . import test_acquire
from .test_acquire import World

pytestmark = [pytest.mark.integration, pytest.mark.contract]
# The acquire suite's fixture, reused: the same organisation, users and rows.
w = test_acquire.w


URL = "/v1/crawl-sources:suggest"
GOAL = "Lãi suất huy động 12 tháng của các ngân hàng thương mại, cập nhật hằng ngày"


def _sources(w: World) -> int:
    return int(w.one("SELECT count(*) FROM acquire.crawl_source WHERE org_id = %s", w.org))


async def test_suggest_queues_the_agent_run_and_registers_nothing(w: World) -> None:
    async with w.client("owner") as c:
        r = await c.post(
            URL, json={"goal": f"  {GOAL} ", "examples": ["https://bank.example.vn/lai-suat"]}
        )
        bare = await c.post(URL, json={"goal": GOAL})
    assert r.status_code == 202, r.text
    run = r.json()["id"]
    assert r.json()["status"] == "queued" and r.json()["poll_after_ms"] > 0
    assert r.headers["Location"] == f"/v1/runs/{run}"
    [(kind, event)] = w.events(run)
    assert kind == "acquire.source.suggest_requested"
    assert event["agent_run_id"] == run and event["data_space_id"] == str(w.space)
    assert event["requested_by"] == str(w.users["owner"])
    assert event["goal"] == GOAL
    assert event["examples"] == ["https://bank.example.vn/lai-suat"]
    assert bare.status_code == 202, bare.text
    assert bare.json()["id"] != run
    assert w.event(bare.json()["id"], "acquire.source.suggest_requested")["examples"] == []
    assert _sources(w) == 0


async def test_admin_data_may_ask_too(w: World) -> None:
    async with w.client("admin") as c:
        r = await c.post(URL, json={"goal": GOAL})
    assert r.status_code == 202, r.text


async def test_suggest_validation(w: World) -> None:
    async with w.client("owner") as c:
        no_goal = await c.post(URL, json={"examples": ["x"]})
        blank = await c.post(URL, json={"goal": "   "})
        empty_example = await c.post(URL, json={"goal": GOAL, "examples": [" "]})
        unknown_field = await c.post(URL, json={"goal": GOAL, "seed_urls": ["https://a.vn"]})
    for r in (no_goal, blank, empty_example, unknown_field):
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "validation_failed"
    assert int(w.one("SELECT count(*) FROM acquire.outbox WHERE org_id = %s", w.org)) == 0


@pytest.mark.security
async def test_suggest_needs_steward_or_admin_data_and_a_data_space(w: World) -> None:
    async with w.client("asker") as c:
        asker = await c.post(URL, json={"goal": GOAL})
    assert asker.status_code == 403, asker.text
    # The data admin is not a member of the other Data Space: it does not exist for them.
    async with w.client("admin", w.other_space) as c:
        outside = await c.post(URL, json={"goal": GOAL})
    assert outside.status_code == 404, outside.text
    assert int(w.one("SELECT count(*) FROM acquire.outbox WHERE org_id = %s", w.org)) == 0
