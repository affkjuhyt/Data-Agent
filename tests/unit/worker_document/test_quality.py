"""DI-13: the deterministic quality checks of a document's text (§9)."""

from __future__ import annotations

import pytest

from services.worker_document import quality as q

pytestmark = [pytest.mark.unit]

PROSE = "Quy chế này quy định lãi suất cho vay đối với khách hàng cá nhân và doanh nghiệp, áp dụng từ ngày ký."


def test_vietnamese_and_english_prose_is_not_garbled() -> None:
    assert not q.garbled(PROSE)
    assert not q.garbled(
        "This policy sets the lending rate for retail and corporate clients alike, from today."
    )


def test_a_broken_text_layer_is_garbled() -> None:
    assert q.garbled("�" * 30 + PROSE[:60])
    assert q.garbled("" * 12 + PROSE[:50])  # private-use glyphs
    assert q.garbled("Мы регулируем процентную ставку " * 4)  # another script than the locale's


def test_a_short_passage_is_never_judged_garbled() -> None:
    assert not q.garbled("�" * 20)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "Trang 3", "12 / 45", ".......... 17 ..........", "123 456 789 012 345 678 901"],
)
def test_a_chunk_with_nothing_to_find_is_empty(text: str) -> None:
    assert q.empty(text)


def test_a_short_sentence_with_words_is_not_empty() -> None:
    assert not q.empty("Điều 3. Hiệu lực thi hành.")
