"""Semantic value retrieval (DeepEye-SQL §3.4): a keyword in the question → the stored value.

`context.value_index` holds the category values of a dimension (filled from a column
profile when the dimension asks for it). Matching is exact on `text.norm`, the same
form `label_norm` stores, longest phrase first, so "Hải Phòng" resolves to the code
the column actually holds and a word the index does not hold is not invented.

A label the question says only in part is found by a second pass (R1-05, the FTS of 01 §3.4): the
words the exact pass left are compared with each label by trigram similarity, as pg_trgm counts it,
and a label at FUZZY_AT or above is a hit, scored by that similarity. "Hồ Chí Minh" finds
"TP. Hồ Chí Minh" (0.80). A one-character typo of a short Vietnamese label scores below it, a
single short word is never compared, and two values a window scores the same for are both left
out: a near match never picks between two values.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from services.worker_agent.understand.text import norm

# A dimension whose column has more distinct values than this is free text. The
# most common strings would be sentences, and a question would match one of them.
MAX_CATEGORY_CARDINALITY = 500
_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class IndexedValue(Protocol):
    @property
    def dimension_code(self) -> str: ...

    @property
    def value_code(self) -> str: ...

    @property
    def label(self) -> str: ...


@dataclass(frozen=True)
class ValueHit:
    dimension_code: str
    value_code: str
    label: str
    score: float
    # The question's words it was read from, normalised: the label itself for an exact hit.
    span: str = ""


FUZZY_AT = 0.8
# A near match must beat every other label on its window by this much, or it is a guess between
# two values ("kho hà nội" is about as near "Kho Hà Nội Một" as "Kho Hà Nội Hai").
_FUZZY_MARGIN = 0.15
# A one-word window is compared only from this length: "nam", "ha", "tp" match too much.
_FUZZY_MIN_WORD = 5


def _trigrams(text: str) -> set[str]:
    """pg_trgm's trigrams: each word padded with two spaces before and one after."""
    out: set[str] = set()
    for word in text.split():
        padded = f"  {word} "
        out |= {padded[i : i + 3] for i in range(len(padded) - 2)}
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


def near_matches(words: Sequence[str], labels: Sequence[str]) -> list[tuple[str, str, float]]:
    """(label, the window of `words` it was found in, score) for each label a window of about its
    length scores FUZZY_AT or more; windows never overlap, the best score is taken first, and a
    window another label scores within _FUZZY_MARGIN of is given to neither. A word masked with
    '#' (used by the exact pass) is not compared."""
    best: dict[str, tuple[float, int, int]] = {}
    for label in dict.fromkeys(labels):
        size = len(label.split())
        for n in {max(1, size - 1), size, size + 1}:
            for i in range(len(words) - n + 1):
                window = words[i : i + n]
                if any("#" in w for w in window):
                    continue
                if n == 1 and len(window[0]) < _FUZZY_MIN_WORD:
                    continue
                score = similarity(" ".join(window), label)
                if score >= FUZZY_AT and score > best.get(label, (0.0, 0, 0))[0]:
                    best[label] = (score, i, i + n)
    by_window: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for label, (score, start, end) in best.items():
        by_window.setdefault((start, end), []).append((score, label))
    taken: set[int] = set()
    out: list[tuple[str, str, float]] = []
    ranked = sorted(by_window.items(), key=lambda item: -max(s for s, _ in item[1]))
    for (start, end), scored in ranked:
        top = max(s for s, _ in scored)
        winners = [label for s, label in scored if s == top]
        window = " ".join(words[start:end])
        runner_up = max(
            (similarity(window, label) for label in labels if label != winners[0]), default=0.0
        )
        if len(winners) > 1 or top - runner_up < _FUZZY_MARGIN or taken & set(range(start, end)):
            continue
        taken |= set(range(start, end))
        out.append((winners[0], window, top))
    return out


@dataclass(frozen=True)
class IndexEntry:
    value_code: str
    label: str
    label_norm: str
    row_count: int


@dataclass(frozen=True)
class IndexDimension:
    """A dimension the profile refresh may fill. Ids are text so the match stays pure."""

    dimension_id: str
    data_space_id: str
    dataset_id: str
    expression: str
    value_index_enabled: bool
    is_time: bool


@dataclass(frozen=True)
class IndexWrite:
    dimension_id: str
    data_space_id: str
    entry: IndexEntry


def index_writes(
    samples: Sequence[tuple[str, str, object]],
    dimensions: Sequence[IndexDimension],
) -> list[IndexWrite]:
    """Which profile values land on which dimension.

    A sample is (dataset id, column name, top_values). A time dimension, a
    dimension with the index off, and an expression that is not one column are
    left out. The same column name on another dataset does not receive the values.
    """
    by_column: dict[tuple[str, str], list[IndexDimension]] = {}
    for dimension in dimensions:
        if not dimension.value_index_enabled or dimension.is_time:
            continue
        column = dimension_column(dimension.expression)
        if column is None:
            continue
        by_column.setdefault((dimension.dataset_id, column), []).append(dimension)
    writes: list[IndexWrite] = []
    for dataset_id, column_name, top_values in samples:
        for dimension in by_column.get((dataset_id, column_name), []):
            for entry in index_entries(top_values):
                writes.append(IndexWrite(dimension.dimension_id, dimension.data_space_id, entry))
    return writes


def value_targets(
    values: Sequence[IndexedValue],
) -> list[tuple[str, tuple[str, ...], float]]:
    """Phrases the value index contributes to the Understander's longest-first pass."""
    found: dict[str, tuple[str, ...]] = {}
    for value in values:
        key = norm(value.label)
        if key and key not in found:
            found[key] = ("value", value.dimension_code, value.value_code)
    return [(phrase, target, 1.0) for phrase, target in found.items()]


def sem_value_retrieval(question: str, values: Sequence[IndexedValue]) -> list[ValueHit]:
    """The stored values whose label is a whole phrase of the question, longest first."""
    text = norm(question)
    labels: dict[str, str] = {}
    for value in values:
        key = norm(value.label)
        if key and key not in labels:
            labels[key] = value.label
    hits: list[ValueHit] = []
    taken = text
    targets = sorted(value_targets(values), key=lambda item: -len(item[0]))
    for phrase, target, score in targets:
        if not re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", taken):
            continue
        hits.append(ValueHit(target[1], target[2], labels.get(phrase, phrase), score, phrase))
        taken = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            lambda match: "#" * len(match.group(0)),
            taken,
        )
    found = {(h.dimension_code, h.value_code) for h in hits}
    rest = {phrase: target for phrase, target, _ in targets if (target[1], target[2]) not in found}
    for phrase, span, score in near_matches(taken.split(), list(rest)):
        target = rest[phrase]
        hits.append(ValueHit(target[1], target[2], labels.get(phrase, phrase), score, span))
    return hits


def dimension_column(expression: str) -> str | None:
    """The column a dimension expression names, or None when it is not one column.

    'branch' and '"branch"' are columns. 'SUM(qty)' is a measure expression and
    must not be filled from a profile.
    """
    text = expression.strip()
    if not text or any(ch in text for ch in "() \n\t"):
        return None
    name = text.rsplit(".", 1)[-1].strip('"')
    return name if _COLUMN.fullmatch(name) else None


def index_entries(top_values: object) -> list[IndexEntry]:
    """Category labels from a column profile. Numbers, dates and masks are not labels."""
    if not isinstance(top_values, list):
        return []
    entries: list[IndexEntry] = []
    for item in top_values:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("value") or "").strip()
        if not _categorical(raw):
            continue
        count = item.get("count")
        entries.append(
            IndexEntry(
                raw,
                raw,
                norm(raw),
                count if isinstance(count, int) else 0,
            )
        )
    return entries


def profile_supplements(
    indexed: Sequence[tuple[str, str]],
    profiles: Sequence[tuple[str, object]],
) -> list[tuple[str, str, str]]:
    """(dimension_code, value_code, label) from column profiles when value_index is empty or thin.

    RES-03: DS-AW (and any space before a profile refresh writes value_index) still has
    `catalog.column_profile.top_values`; those labels must feed `_named_members` so a dropped
    filter ("Bikes") is caught before compose.
    """
    have = set(indexed)
    out: list[tuple[str, str, str]] = []
    for dimension_code, top_values in profiles:
        for entry in index_entries(top_values):
            key = (dimension_code, entry.value_code)
            if key in have:
                continue
            have.add(key)
            out.append((dimension_code, entry.value_code, entry.label))
    return out


def _categorical(value: str) -> bool:
    if value == "***" or not 2 <= len(value) <= 80:
        return False
    if not any(char.isalpha() for char in value):
        return False
    return re.fullmatch(r"[\d./:-]+", value) is None
