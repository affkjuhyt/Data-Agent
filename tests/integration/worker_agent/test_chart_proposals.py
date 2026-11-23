"""AL-03: a chart the validator blocked over a measure declared additive whose SQL is not opens
one measure_additivity proposal; accepted, the steward's declaration becomes `none`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.worker_agent import chart_proposals

from .conftest import Seed
from .test_answers import build, warehouse  # noqa: F401  (fixture)
from .test_metric_changes import decide

pytestmark = [pytest.mark.integration, pytest.mark.req("AL-03")]

PROPOSAL = {
    "kind": "measure_additivity",
    "metric_id": None,
    "measure_code": "du_no",
    "from": "semi",
    "to": "none",
}


async def test_a_blocked_share_files_one_proposal_and_accepting_declares_the_measure(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)

    async def file() -> uuid.UUID | None:
        return await chart_proposals.file(
            org_id=bank.org,
            data_space_id=bank.space,
            user_id=bank.user,
            proposal=PROPOSAL,
            reason="“Dư nợ” không cộng được giữa các nhóm, nên không chia thành tỷ trọng",
        )

    first, again = await file(), await file()
    # The id is the measure's: a second blocked chart files nothing new.
    assert first is not None and first == again
    measure = seed.one(
        "SELECT id FROM context.semantic_measure WHERE data_space_id = %s AND code = 'du_no'",
        bank.space,
    )
    kind, status, target, diff = seed.one(
        "SELECT kind, status, target_ref, diff FROM inbox.proposal WHERE id = %s", first
    )
    assert (kind, status, target) == ("measure_additivity", "pending_review", measure)
    assert (diff["from"], diff["to"], diff["measure_code"]) == ("semi", "none", "du_no")
    assert seed.one("SELECT count(*) FROM inbox.proposal WHERE target_ref = %s", measure) == 1

    await decide(bank, first, "accepted")
    assert seed.one("SELECT additivity FROM context.semantic_measure WHERE id = %s", measure) == (
        "none"
    )
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", first) == "applied"
    # Declared `none` now: nothing more to file.
    assert await file() is None


async def test_a_declaration_changed_while_the_proposal_waited_is_not_overwritten(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = await chart_proposals.file(
        org_id=bank.org,
        data_space_id=bank.space,
        user_id=bank.user,
        proposal={**PROPOSAL, "measure_code": "no_xau"},
        reason="r",
    )
    assert pid is not None
    seed.conn.cursor().execute(
        "UPDATE context.semantic_measure SET additivity = 'full'"
        " WHERE data_space_id = %s AND code = 'no_xau'",
        (str(bank.space),),
    )
    await decide(bank, pid, "accepted")
    assert seed.one("SELECT status FROM inbox.proposal WHERE id = %s", pid) == "apply_failed"
    assert (
        seed.one(
            "SELECT additivity FROM context.semantic_measure WHERE data_space_id = %s"
            " AND code = 'no_xau'",
            bank.space,
        )
        == "full"
    )
