"""Semantic value retrieval (DeepEye-SQL §3.4): a keyword in the question → the stored value.

`context.value_index` holds the category values of a dimension (filled from a column
profile when the dimension asks for it). Matching is exact on `text.norm`, the same
form `label_norm` stores, longest phrase first, so "Hải Phòng" resolves to the code
the column actually holds and a word the index does not hold is not invented.

A near spelling stays unmapped. The trigram ≥ 0.8 / Qdrant ≥ 0.82 pass named in the
Understander belongs to the later model path; a one-character typo of a short
Vietnamese label scores well below 0.8, so guessing it here would attach the wrong
filter.
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
    for phrase, target, score in sorted(value_targets(values), key=lambda item: -len(item[0])):
        if not re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", taken):
            continue
        hits.append(ValueHit(target[1], target[2], labels.get(phrase, phrase), score))
        taken = re.sub(
            rf"(?<!\w){re.escape(phrase)}(?!\w)",
            lambda match: "#" * len(match.group(0)),
            taken,
        )
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


def _categorical(value: str) -> bool:
    if value == "***" or not 2 <= len(value) <= 80:
        return False
    if not any(char.isalpha() for char in value):
        return False
    return re.fullmatch(r"[\d./:-]+", value) is None
