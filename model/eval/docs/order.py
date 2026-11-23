"""Reading-order normalized edit distance of a page, in [0, 1] (0 = the gold's order).

As OmniDocBench computes it (src/dataset/end2end_dataset.py `get_order_paired`, commit
7ddb8df411ad9bd2fff139acafe0ed16aa1694fe, Apache-2.0; see NOTICE): each gold element has its
reading positions (the gold's `order`, 1..n; several when a prediction merged gold elements) and
the position of the predicted element it was matched with, or none. The gold's order is every
gold position sorted; the prediction's is the matched gold positions sorted by where the
prediction put them. Their Levenshtein distance over max(len) is the page's score; an element the
prediction missed counts as a deletion. The distance is rapidfuzz's (MIT) rather than the
`Levenshtein` package's (GPL-2.0, forbidden by tools/licenses.json): the same distance.
Matching gold elements to predicted ones is the bake-off's (DI-05), not this module's."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein


@dataclass(frozen=True)
class Matched:
    """One gold element (or several a prediction merged): its gold positions and where the
    prediction placed it, None when the prediction has no element for it."""

    gold: Sequence[int]
    predicted: float | None


def edit(matches: Sequence[Matched]) -> float | None:
    """The page's normalized edit distance; None when the page has no element on either side."""
    gold = sorted(p for m in matches for p in m.gold)
    placed = sorted(
        (m for m in matches if m.gold and m.predicted is not None), key=lambda m: m.predicted
    )  # type: ignore[arg-type, return-value]
    predicted = [p for m in placed for p in m.gold]
    if not gold and not predicted:
        return None
    return Levenshtein.distance(gold, predicted) / max(len(gold), len(predicted))


def mean(pages: Sequence[float | None]) -> float | None:
    """The documents' score: the mean over pages that have elements (OmniDocBench's page
    average)."""
    scored = [p for p in pages if p is not None]
    return sum(scored) / len(scored) if scored else None
