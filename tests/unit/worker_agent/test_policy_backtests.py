from __future__ import annotations

import uuid

from libs.dataagent_db.models import governance
from services.worker_agent.policy_backtests import _applies, _mentions_table


def test_ac_11_02_02_policy_backtest_matches_direct_user() -> None:
    policy = governance.AccessPolicy(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        name="branch policy",
        kind="row",
        applies_to={"user_ids": ["00000000-0000-0000-0000-000000000001"]},
        status="draft",
    )
    assert _applies(policy, uuid.UUID(int=2), set(), set()) is False
    assert _applies(policy, uuid.UUID(int=1), set(), set()) is True


def test_ac_11_02_02_policy_backtest_matches_table_without_running_sql() -> None:
    assert _mentions_table("SELECT * FROM public.customer_balance", "postgres", "customer_balance")
    assert not _mentions_table("SELECT * FROM public.other_table", "postgres", "customer_balance")
