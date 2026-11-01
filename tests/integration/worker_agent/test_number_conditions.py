"""A condition on a number in the question (GEN-22, read by the packs of GEN-31): "số dư khoản vay
trên 500" on the bank of test_answers.py is a filter on the balance column, in Vietnamese and in
English, kept as a plan chip, and refused when the metric does not allow the dimension (BR-81)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import answer_of, build, drain
from .test_follow_ups import plan_of

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def numbered(seed: Seed, bank: test_answers.Bank, *, allowed: bool = True) -> None:
    """The loan balance as a number dimension, as a schema scan and a steward leave it."""
    seed.row(
        "catalog.column_meta",
        org_id=bank.org,
        dataset_id=bank.loans,
        dataset_version=1,
        name="balance",
        ordinal=0,
        data_type="numeric",
        normalized_type="decimal",
    )
    loan_entity = seed.one(
        "SELECT id FROM context.semantic_entity WHERE primary_dataset_id = %s", bank.loans
    )
    dim = seed.row(
        "context.semantic_dimension",
        org_id=bank.org,
        data_space_id=bank.space,
        entity_id=loan_entity,
        code="so_du_khoan",
        name="số dư khoản vay",
        expression="balance",
    )
    if allowed:
        with seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE context.metric_version v SET allowed_dimensions = allowed_dimensions || %s::uuid"
                " FROM context.metric m WHERE m.id = v.metric_id AND m.data_space_id = %s",
                (str(dim), str(bank.space)),
            )


@pytest.mark.parametrize(
    "question",
    [
        "Dư nợ có số dư khoản vay trên 500 tháng 9/2026",
        "Dư nợ with a số dư khoản vay over 500 in September 2026",
    ],
)
async def test_a_balance_over_a_number_is_a_filter_on_the_column(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    numbered(seed, bank)
    q = bank.ask(question)
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    # 30/09, without internal lending: 800 + 1000 + 900; the 200 and the 100 are not over 500.
    assert [float(f["value"]) for f in figures] == [2700.0]
    chips, _ = plan_of(seed, q)
    [condition] = [c for c in chips if c["kind"] == "condition"]
    assert json.loads(condition["value"]) == {
        "dimension": "so_du_khoan",
        "op": ">",
        "values": [500],
    }


async def test_a_condition_on_a_dimension_the_metric_does_not_allow_is_refused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    numbered(seed, bank, allowed=False)
    q = bank.ask("Dư nợ có số dư khoản vay trên 500 tháng 9/2026")
    await drain()
    status, kind, text, _, _, _ = seed.one(
        "SELECT q.status, a.kind, a.text, a.figures, a.product_scope, a.id FROM qa.question q "
        "LEFT JOIN qa.answer a ON a.question_id = q.id WHERE q.id = %s",
        q,
    )
    assert kind != "data", text
    assert "số dư khoản vay" in (text or ""), text
