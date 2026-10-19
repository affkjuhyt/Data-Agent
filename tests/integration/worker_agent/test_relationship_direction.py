"""A relationship declared from its "one" side (one_to_many) is joined the other way round
(semantic/loader.py), so a cut by the branch's region answers as when it is declared many-to-one."""

from __future__ import annotations

from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import activate, answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


async def test_a_one_to_many_relationship_is_joined_from_its_many_side(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    before = bank.ask("Dư nợ theo vùng tháng 9/2026")
    await drain()
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE context.relationship SET from_dataset_id = to_dataset_id,"
            " to_dataset_id = from_dataset_id, from_columns = to_columns,"
            " to_columns = from_columns, cardinality = 'one_to_many' WHERE org_id = %s",
            (str(bank.org),),
        )
    after = bank.ask("Dư nợ theo vùng tháng 9/2026")
    await drain()

    def by_region(question: Any) -> dict[str, float]:
        status, kind, text, figures, _, _ = answer_of(seed, question)
        assert (status, kind) == ("answered", "data"), text
        return {
            f["dimensions"]["vung"]: float(f["value"]) for f in figures if "expr" not in f["source"]
        }

    assert by_region(after) == by_region(before) == {"Miền Bắc": 2000.0, "Miền Nam": 1000.0}
