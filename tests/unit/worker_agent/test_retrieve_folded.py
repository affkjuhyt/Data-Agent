"""DI-11: the keyword half of document retrieval reads a question without diacritics (the folded
branch) and widens it with the Data Space's synonyms (§6.2)."""

from __future__ import annotations

import pytest

from services.worker_agent.documents import retrieve as r

pytestmark = [pytest.mark.unit]


def test_the_two_branches_hold_the_words_as_written_and_folded() -> None:
    assert r._tsquery("Lãi suất cho vay là gì?") == "lãi | suất | vay"
    assert r._folded_tsquery("Lãi suất cho vay là gì?") == "lai | suat | vay"
    # A question typed without diacritics folds to the same words.
    assert r._folded_tsquery("lai suat cho vay la gi") == "lai | suat | vay"
    assert r._tsquery("là gì?") is None and r._folded_tsquery("là gì?") is None


def test_relevance_reads_words_without_their_diacritics() -> None:
    # The bars (overlap, coverage) must not drop what the folded branch found.
    assert r.overlap("lai suat tiet kiem", "Lãi suất tiết kiệm kỳ hạn 12 tháng") == 1.0
    assert r.overlap("Đơn vị tính", "don vi tinh") == 1.0
    # A stop word stays a stop word typed either way; a folded form that is also a subject word
    # does not ("vay" of "cho vay" is not "vậy").
    assert r._words("la gi") == []
    assert r._words("vay von") == ["vay", "von"]


SYNONYMS = [
    ("doanh thu", ("metric", "m1", None)),
    ("doanh số", ("metric", "m1", None)),
    ("revenue", ("metric", "m1", None)),
    ("chi nhánh", ("dimension", "d1", None)),
    ("phòng giao dịch", ("dimension", "d1", None)),
    ("nợ xấu", ("metric", "m2", None)),
]


def test_a_phrase_brings_the_other_phrases_of_its_target() -> None:
    assert r.expand("Doanh so nam 2024 theo chi nhanh", SYNONYMS) == [
        "doanh thu",
        "revenue",
        "phòng giao dịch",
    ]
    # A phrase is matched on word boundaries: "nợ" alone names nothing.
    assert r.expand("Nợ quá hạn", SYNONYMS) == []
    expanded = r.expand("doanh thu", SYNONYMS)
    assert r._tsquery("doanh thu", expanded) == "doanh | revenue | số | thu"


def test_the_expansion_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    many = [(f"tên {n}", ("metric", "m", None)) for n in range(20)] + [
        ("doanh thu", ("metric", "m", None))
    ]
    assert len(r.expand("doanh thu", many)) == r.MAX_EXPANSION
