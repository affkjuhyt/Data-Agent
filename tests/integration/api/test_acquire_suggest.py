"""POST /v1/crawl-sources:suggest (UC-14-01, added 12/10/2026) against PostgreSQL.

Proposing sources for a goal is the Agent Thu thập run's job, and that run needs a web search the
stack does not have: after its checks the api answers 501 not_available and writes nothing, no
outbox event and no source. Organisation and users come from the acquire suite's fixture.
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


def _events(w: World) -> int:
    return int(w.one("SELECT count(*) FROM acquire.outbox WHERE org_id = %s", w.org))


async def test_suggest_is_refused_until_a_web_search_exists(w: World) -> None:
    async with w.client("owner") as c:
        r = await c.post(
            URL, json={"goal": f"  {GOAL} ", "examples": ["https://bank.example.vn/lai-suat"]}
        )
    async with w.client("admin") as c:
        admin = await c.post(URL, json={"goal": GOAL})
    for refused in (r, admin):
        assert refused.status_code == 501, refused.text
        assert refused.json()["code"] == "not_available" and refused.json()["detail"]
    assert _events(w) == 0 and _sources(w) == 0


async def test_suggest_validation(w: World) -> None:
    async with w.client("owner") as c:
        no_goal = await c.post(URL, json={"examples": ["x"]})
        blank = await c.post(URL, json={"goal": "   "})
        empty_example = await c.post(URL, json={"goal": GOAL, "examples": [" "]})
        unknown_field = await c.post(URL, json={"goal": GOAL, "seed_urls": ["https://a.vn"]})
    for r in (no_goal, blank, empty_example, unknown_field):
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "validation_failed"
    assert _events(w) == 0


@pytest.mark.security
async def test_suggest_needs_steward_or_admin_data_and_a_data_space(w: World) -> None:
    async with w.client("asker") as c:
        asker = await c.post(URL, json={"goal": GOAL})
    assert asker.status_code == 403, asker.text
    # The data admin is not a member of the other Data Space: it does not exist for them.
    async with w.client("admin", w.other_space) as c:
        outside = await c.post(URL, json={"goal": GOAL})
    assert outside.status_code == 404, outside.text
    assert _events(w) == 0
