"""DI-02: the bake-off's evaluators (model/eval/docs): TEDS/TEDS-S, reading-order edit distance,
CER/WER. The gold scored against itself is 1.0 (TEDS) or 0 (distances), and known edits cost
what the definitions say."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from model.eval.docs import cer, order
from model.eval.docs.teds import TEDS

pytestmark = [pytest.mark.unit]

HERE = pathlib.Path(__file__).parents[3] / "model" / "eval" / "docs" / "labelstudio"
_spec = importlib.util.spec_from_file_location("bakeoff_convert_teds", HERE / "convert.py")
assert _spec is not None and _spec.loader is not None
lc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lc
_spec.loader.exec_module(lc)


def _table(rows: list[list[str]], *, span: bool = False) -> str:
    """A gold table as convert.py writes it: a header cell spanning two columns when `span`."""
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if span and r == 0 and c == 1:
                continue
            wide = span and r == 0 and c == 0
            cells.append(lc.Cell(r, c, 1, 2 if wide else 1, text, r == 0))
    return lc.table_html(cells)


GOLD = _table([["Chỉ tiêu", "Năm 2024"], ["Doanh thu", "1.234,5"], ["Lợi nhuận", "987"]])


def test_the_gold_against_itself_is_one() -> None:
    assert TEDS().evaluate(GOLD, GOLD) == 1.0
    assert TEDS(structure_only=True).evaluate(GOLD, GOLD) == 1.0
    assert order.edit([order.Matched([1], 0), order.Matched([2], 1)]) == 0.0
    assert cer.cer("Điều 5. Lãi suất", "Điều 5. Lãi suất") == 0.0


def test_a_wrong_cell_costs_its_text_and_not_the_structure() -> None:
    wrong = _table([["Chỉ tiêu", "Năm 2024"], ["Doanh thu", "1.234,6"], ["Lợi nhuận", "987"]])
    full, structure = TEDS().evaluate(wrong, GOLD), TEDS(structure_only=True).evaluate(wrong, GOLD)
    assert structure == 1.0 and 0.9 < full < 1.0


def test_a_missing_row_or_a_lost_span_costs_structure() -> None:
    short = _table([["Chỉ tiêu", "Năm 2024"], ["Doanh thu", "1.234,5"]])
    assert TEDS(structure_only=True).evaluate(short, GOLD) < 1.0
    merged = _table(
        [["Chỉ tiêu", "Năm 2024"], ["Doanh thu", "1.234,5"], ["Lợi nhuận", "987"]], span=True
    )
    assert TEDS(structure_only=True).evaluate(merged, GOLD) < 1.0


def test_no_table_scores_zero() -> None:
    assert TEDS().evaluate("", GOLD) == 0.0
    assert TEDS().evaluate("<html><body><p>x</p></body></html>", GOLD) == 0.0


def test_reading_order_counts_swaps_and_misses() -> None:
    # Gold 1,2,3,4; the prediction read 2 before 1 and missed 4.
    page = [
        order.Matched([1], 1.0),
        order.Matched([2], 0.0),
        order.Matched([3], 2.0),
        order.Matched([4], None),
    ]
    # [1,2,3,4] → [2,1,3]: two substitutions and one deletion over 4.
    assert order.edit(page) == pytest.approx(3 / 4)
    # A prediction that merged gold 2 and 3 into one element keeps their order.
    assert order.edit([order.Matched([1], 0), order.Matched([2, 3], 1)]) == 0.0
    assert order.edit([]) is None
    assert order.mean([0.0, None, 0.5]) == 0.25


def test_cer_and_wer_with_and_without_diacritics() -> None:
    gold = "Điều 5. Lãi suất cho vay"
    lost_marks = "Dieu 5. Lai suat cho vay"
    assert cer.cer(gold, lost_marks, accents=False) == 0.0
    assert cer.cer(gold, lost_marks) == pytest.approx(4 / len(gold))
    assert cer.wer(gold, lost_marks) == pytest.approx(3 / 6)
    assert cer.wer(gold, lost_marks, accents=False) == 0.0
    # Whitespace and composition do not count: NFD input, doubled spaces.
    import unicodedata

    assert cer.cer(gold, unicodedata.normalize("NFD", "Điều  5. Lãi suất cho vay")) == 0.0
    # Added text is an insertion: CER can pass 1, the normalized edit distance cannot.
    assert cer.cer("ab", "abcdef") == 2.0 and cer.edit_dist("ab", "abcdef") == pytest.approx(4 / 6)
    assert cer.edit_dist("", "") is None


def test_the_corpus_rate_weighs_pages_by_their_length() -> None:
    pages = [cer.chars("aaaa", "aaab"), cer.chars("aaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaa")]
    assert cer.total(pages) == pytest.approx(1 / 20)
