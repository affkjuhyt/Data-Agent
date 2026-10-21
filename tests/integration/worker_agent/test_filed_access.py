"""A filed figure is the period's total (questions._filed_restricted): it is answered to someone
who may see the whole, refused to someone a row scope or an access policy limits, since it cannot
be filtered for them; and a filed question without a period is refused, not a crash."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, activate, answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def file_september(bank: Bank) -> None:
    version = bank.seed.one(
        "SELECT v.id FROM context.metric_version v JOIN context.metric m ON m.id = v.metric_id"
        " WHERE m.org_id = %s AND m.code = 'DU_NO'",
        bank.org,
    )
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
        value=3000,
        report_ref="NHNN-T9",
        sha256="cd" * 32,
    )


def ask_filed(bank: Bank, text: str) -> uuid.UUID:
    question = bank.ask(text)
    with bank.seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE qa.question SET knowledge_mode = 'period_close' WHERE id = %s",
            (str(question),),
        )
    return question


async def test_the_filed_figure_is_answered_to_whoever_may_see_the_whole(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank, metrics=("DU_NO",))
    file_september(bank)
    q = ask_filed(bank, "Dư nợ tháng 9/2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert [float(f["value"]) for f in figures if "expr" not in f["source"]] == [3000.0]


async def test_the_filed_figure_is_refused_to_someone_a_row_policy_limits(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank, metrics=("DU_NO",))
    file_september(bank)
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Chỉ khoản vay bán lẻ",
        kind="row",
        applies_to=json.dumps({}),
        condition_expr="group_code = 1",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    q = ask_filed(bank, "Dư nợ tháng 9/2026")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal"), text
    assert "không lọc được theo phạm vi" in text


async def test_a_filed_question_without_a_period_is_refused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank, metrics=("DU_NO",))
    q = ask_filed(bank, "Dư nợ")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal"), text
    assert "nêu rõ kỳ chốt" in text
