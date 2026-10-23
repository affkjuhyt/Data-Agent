"""R0-8 on the answer path: an answered question carries its deterministic checks in
number_check.verification, written once with the answer; a refusal carries none."""

from __future__ import annotations

from typing import Any

import pytest

from libs.contracts.v1.verification import VerificationFrame

from . import test_answers
from .conftest import Seed
from .test_answers import activate, answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def checks_of(seed: Seed, answer_id: Any) -> dict[str, Any]:
    return seed.one("SELECT number_check FROM qa.answer WHERE id = %s", answer_id)  # type: ignore[return-value]


async def test_an_answer_carries_one_verification_that_passes(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    q = bank.ask("Tỷ lệ nợ xấu theo vùng tháng 9/2026")
    await drain()
    status, kind, text, figures, _, answer_id = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    frame = VerificationFrame.model_validate(checks_of(seed, answer_id)["verification"])
    assert frame.verdict == "PASS", frame.checks
    assert seed.one("SELECT count(*) FROM qa.answer WHERE question_id = %s", q) == 1


async def test_a_refusal_carries_no_verification(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    q = bank.ask("Giá vàng hôm nay là bao nhiêu?")
    await drain()
    status, kind, _, _, _, answer_id = answer_of(seed, q)
    assert kind in ("refusal", "clarification"), (status, kind)
    assert "verification" not in (checks_of(seed, answer_id) or {})


async def test_a_refusal_keeps_its_code_and_what_to_do_about_it(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # R0-7: the code reaches the Ask screen from the stored answer, not only from the live stream.
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Doanh thu phí bảo hiểm tháng này")
    await drain()
    status, kind, _, _, _, answer_id = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal")
    refusal = checks_of(seed, answer_id)["refusal"]
    assert refusal["code"] == "UNRESOLVED_ENTITY" and refusal["next_action"] in (
        "rephrase",
        "contact_steward",
    )
