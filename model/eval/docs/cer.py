"""Text accuracy of OCR and extraction against the gold: CER, WER and the normalized edit distance.

CER and WER are the usual rates, edits over the gold's length (characters, words), so they can pass
1 when the prediction adds text; summed over pages they are edits over the corpus's length, as the
published Tesseract CER for Vietnamese (0.14, DOCUMENT_INTELLIGENCE_SPEC §5) is. `edit_dist` is
OmniDocBench's text metric (src/metrics/cal_metric.py `Edit_dist`, commit
7ddb8df411ad9bd2fff139acafe0ed16aa1694fe, Apache-2.0; see NOTICE): edits over the longer side, in
[0, 1]. The distance is rapidfuzz's Levenshtein (MIT).

Both sides are NFC with whitespace runs folded to one space (the gold's OCR pages are NFC,
labelstudio/convert.py). `accents=False` scores the same text without Vietnamese diacritics
(NFD with combining marks dropped, đ → d): an engine that reads the letters but loses the tone
marks scores well there and badly with them, which tells the two failures apart.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

_SPACE = re.compile(r"\s+")


def normalize(text: str, *, accents: bool = True) -> str:
    text = _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()
    if accents:
        return text
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )
    return stripped.replace("đ", "d").replace("Đ", "D")


@dataclass(frozen=True)
class Count:
    """Edits and the gold's length, which add up over pages."""

    edits: int
    length: int

    @property
    def rate(self) -> float:
        return self.edits / self.length if self.length else float(self.edits > 0)


def chars(gold: str, predicted: str, *, accents: bool = True) -> Count:
    g, p = normalize(gold, accents=accents), normalize(predicted, accents=accents)
    return Count(Levenshtein.distance(g, p), len(g))


def words(gold: str, predicted: str, *, accents: bool = True) -> Count:
    g = normalize(gold, accents=accents).split()
    p = normalize(predicted, accents=accents).split()
    return Count(Levenshtein.distance(g, p), len(g))


def cer(gold: str, predicted: str, *, accents: bool = True) -> float:
    return chars(gold, predicted, accents=accents).rate


def wer(gold: str, predicted: str, *, accents: bool = True) -> float:
    return words(gold, predicted, accents=accents).rate


def total(counts: Iterable[Count]) -> float:
    """The corpus rate: every page's edits over every page's gold length."""
    edits = length = 0
    for c in counts:
        edits += c.edits
        length += c.length
    return Count(edits, length).rate


def edit_dist(gold: str, predicted: str) -> float | None:
    """OmniDocBench's normalized edit distance; None when both sides are empty."""
    g, p = normalize(gold), normalize(predicted)
    if not g and not p:
        return None
    return Levenshtein.distance(g, p) / max(len(g), len(p))
