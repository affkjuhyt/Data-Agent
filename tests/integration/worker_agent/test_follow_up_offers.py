"""The next questions an answer offers (suggestions.py) on the bank of test_answers.py: stored on
the answer, and each one, asked as a click would ask it, is answered with data."""

from __future__ import annotations

from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import answer_of, build, drain

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


async def test_every_offered_question_is_answered(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ tháng 9/2026")
    await drain()
    status, kind, text, _, _, answer_id = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    offered = seed.one("SELECT follow_ups FROM qa.answer WHERE id = %s", answer_id)
    assert isinstance(offered, list) and len(offered) >= 2, offered
    clicks = [bank.ask(question) for question in offered]
    await drain()
    for question, asked in zip(offered, clicks, strict=True):
        status, kind, text, _, _, _ = answer_of(seed, asked)
        assert (status, kind) == ("answered", "data"), (question, text)
