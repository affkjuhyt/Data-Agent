"""UC-16-02: worker-agent answers a leader's private question from Redis and keeps nothing of it
in a table (BR-193): no qa.* row, no stored result rows, every key gone after 15 minutes."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import private

from . import test_answers
from .conftest import Seed
from .test_answers import build

# The bank schema of the end-to-end answer tests, shared rather than built twice.
warehouse = test_answers.warehouse

pytestmark = [pytest.mark.integration]


@pytest.fixture
async def redis(redis_container: dict[str, str]) -> Any:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_container["url"], decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


async def _events(redis: Any, qid: str) -> list[tuple[str, dict[str, Any]]]:
    entries = await redis.xrange(f"exec:pq:{qid}:events", "-", "+")
    return [(f["event"], json.loads(f["data"])) for _, f in entries]


async def test_uc_16_02_a_private_question_is_answered_and_nothing_is_kept(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    redis: Any,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    qid = str(uuid.uuid4())
    await redis.set(
        f"exec:pq:{qid}",
        json.dumps(
            {
                "id": qid,
                "org_id": str(bank.org),
                "owner_id": str(bank.user),
                "text": "Tỷ lệ nợ xấu theo vùng tháng 9/2026",
                "context": [],
                "as_of": None,
                "data_space_id": str(bank.space),
            }
        ),
        ex=900,
    )
    await private.handle(redis, qid)
    events = await _events(redis, qid)
    assert [e for e, _ in events] == ["progress", "answer", "done"]
    answer = events[1][1]
    assert [f["value"][:4] for f in answer["figures"]] == ["10.0", "10.0"] and "Miền Bắc" in answer[
        "text"
    ]
    run = answer["figures"][0]["query_run_id"]
    # The run is on record (SQL, hashes); its rows and the question are not.
    assert seed.one("SELECT status, caller_kind FROM query.query_run WHERE id = %s", run) == (
        "succeeded",
        "ad_hoc",
    )
    assert seed.one("SELECT count(*) FROM query.query_result WHERE query_run_id = %s", run) == 0
    assert seed.one("SELECT count(*) FROM qa.question WHERE org_id = %s", bank.org) == 0
    assert 0 < await redis.ttl(f"exec:pq:{qid}:events") <= 900


async def test_uc_16_02_without_a_data_space_says_so(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    redis: Any,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    qid = str(uuid.uuid4())
    await redis.set(
        f"exec:pq:{qid}",
        json.dumps(
            {
                "id": qid,
                "org_id": str(bank.org),
                "owner_id": str(bank.user),
                "text": "x",
                "context": [],
                "as_of": None,
                "data_space_id": None,
            }
        ),
        ex=900,
    )
    await private.handle(redis, qid)
    events = await _events(redis, qid)
    assert events[1] == (
        "error",
        {"code": "data_space_required", "message": events[1][1]["message"]},
    )


async def test_uc_16_02_an_expired_question_is_left_alone(redis: Any) -> None:
    qid = str(uuid.uuid4())
    await private.handle(redis, qid)
    assert await _events(redis, qid) == []
