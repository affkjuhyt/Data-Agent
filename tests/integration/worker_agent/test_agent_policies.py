"""A policy for the agent only (applies_to.actors, query/scope.py): what it hides never reaches the
agent's answers, while the same person reading the data themselves still sees it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from services.worker_agent.query.scope import active_policies, actor_of

from . import test_answers
from .conftest import Seed
from .test_answers import activate, answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def agent_only_policy(seed: Seed, bank: test_answers.Bank) -> None:
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Agent chỉ thấy khoản vay bán lẻ",
        kind="row",
        applies_to=json.dumps({"actors": ["agent"]}),
        condition_expr="group_code = 1",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )


def test_who_runs_a_query() -> None:
    assert actor_of("question", "human") == "agent"
    assert actor_of("receipt_replay", "human") == "agent"
    assert actor_of("ad_hoc", "human") == "person"
    assert actor_of("tile", "agent") == "agent"  # the agent's own account
    assert actor_of("period", "human") is None  # the one vetted system caller
    # Unknown or user-authored callers fail closed to the stricter reading.
    assert actor_of(None, "human") == "agent"
    assert actor_of("pipeline", "human") == "agent"
    assert actor_of("something_new", "human") == "agent"


async def test_an_agent_only_policy_applies_to_the_agent_and_not_to_the_person(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    agent_only_policy(seed, bank)
    async with scoped_session("svc_query", bank.org, bank.user) as session:
        dataset = await session.get(catalog.Dataset, bank.loans)
        assert dataset is not None

        async def count(caller_kind: str) -> int:
            return len(
                await active_policies(
                    session, principal_id=bank.user, datasets=[dataset], caller_kind=caller_kind
                )
            )

        assert await count("question") == 1
        assert await count("ad_hoc") == 0
        assert await count("pipeline") == 1
        assert await count("period") == 0

    question = bank.ask("Tỷ lệ nợ xấu tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, question)
    assert (status, kind) == ("answered", "data"), text
    executed = seed.one("SELECT sql_executed FROM query.query_run WHERE caller_id = %s", question)
    # Only group 1 reaches the agent, so no loan is bad: the NPL ratio it answers is 0.
    assert "group_code" in executed
    assert float(figures[0]["value"]) == pytest.approx(0.0)
