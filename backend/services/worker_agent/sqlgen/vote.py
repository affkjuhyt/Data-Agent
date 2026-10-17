"""The quality gate of the exploratory SQL path (01 §3.4): every candidate that passed its checks
runs, results are grouped by what they return, and the share of the six candidates in the
largest group is the confidence. Chốt 06/10/2026: ≥ 4/6 answers with the "Khám phá" label, 3/6
answers asking to check it, ≤ 2/6 gives no number and asks back; all failed is a coded error.
"""

from __future__ import annotations

import decimal
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

CANDIDATES = 6
ANSWER_AT = 4
REVIEW_AT = 3

Decision = Literal["answer", "review", "ask", "failed"]


def _value(v: Any) -> str:
    try:
        d = decimal.Decimal(str(v))
    except (decimal.InvalidOperation, ValueError):
        return str(v).strip()
    if not d.is_finite():
        return str(v)
    return format(d.quantize(decimal.Decimal("0.0001")).normalize(), "f")


def signature(rows: Sequence[Sequence[Any]]) -> str:
    """What a result says, whatever the order of its rows or how its numbers were rounded."""
    body = sorted(json.dumps([_value(v) for v in row], ensure_ascii=False) for row in rows)
    return hashlib.sha256("\n".join(body).encode()).hexdigest()


@dataclass(frozen=True)
class Vote:
    decision: Decision
    confidence: float
    winner: int | None  # the index of the first candidate of the largest group


def vote(results: Sequence[Sequence[Sequence[Any]] | None], total: int = CANDIDATES) -> Vote:
    groups: dict[str, list[int]] = {}
    for i, rows in enumerate(results):
        if rows is not None:
            groups.setdefault(signature(rows), []).append(i)
    if not groups:
        return Vote("failed", 0.0, None)
    best = max(groups.values(), key=lambda g: (len(g), -g[0]))
    n = len(best)
    decision: Decision = "answer" if n >= ANSWER_AT else "review" if n >= REVIEW_AT else "ask"
    return Vote(decision, n / total, best[0])
