"""The quality gate of the exploratory SQL path (01 §3.4): every candidate that passed its checks
runs, results are grouped by what they return, and the share of the six candidates in the
largest group is the confidence. 6/6 answers with the "Khám phá" label, 3/6 to 5/6 answers asking
to check it, ≤ 2/6 gives no number and asks back; all failed is a coded error. Until 24/11 4/6
answered without the check: measured on DS-AW (19/11, docs/v3/eval/2026-11-19-calibration-
baseline.md) the winner was right 11 of 16 times at 6/6 and 0 of 9 at 4/6 and 5/6, so a 4/6 or 5/6
answer now says it should be checked. No question asks back more for it: review answers too.

Which group wins is counted by way of writing (AE-23, CONFIDENCE_SPEC §4): each way (skeleton,
examples, divide) gives its weight, logit of its accuracy in the last ablation, to the groups its
two samples fell in, so two samples of one way that agree are one vote, not two. `margin` is how
far the winner is ahead of every way's weight; the decision and `confidence` stay the share k of
the candidates in the winning group, the meter R1-14 shows, until AE-24 calibrates them.
SQLGEN_VOTE=count picks the largest group again (the rollback).
"""

from __future__ import annotations

import decimal
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from libs.calibration import method_vote

CANDIDATES = 6
ANSWER_AT = 6
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
    confidence: float  # k / total: the share of the candidates in the winning group
    winner: int | None  # the index of the first candidate of the winning group
    margin: float = 0.0  # (S1 - S2) / W of the vote by way of writing (§4)
    support: dict[str, float] = field(default_factory=dict)  # per way: its share in the winner


def vote(
    results: Sequence[Sequence[Sequence[Any]] | None],
    total: int = CANDIDATES,
    methods: Sequence[str] | None = None,
    weights: Mapping[str, float] | None = None,
) -> Vote:
    """`methods[i]` is the way candidate i was written; without them each candidate is its own
    way of weight 1, which is the count vote."""
    signatures = [signature(rows) if rows is not None else None for rows in results]
    groups: dict[str, list[int]] = {}
    for i, sig in enumerate(signatures):
        if sig is not None:
            groups.setdefault(sig, []).append(i)
    if not groups:
        return Vote("failed", 0.0, None)
    ways = list(methods) if methods is not None else [f"#{i}" for i in range(len(results))]
    counted = method_vote(signatures, ways, weights if methods is not None else None)
    if os.getenv("SQLGEN_VOTE", "method") == "count":
        best = max(groups.values(), key=lambda g: (len(g), -g[0]))
    else:
        assert counted.winner is not None
        best = groups[signatures[counted.winner] or ""]
    n = len(best)
    decision: Decision = "answer" if n >= ANSWER_AT else "review" if n >= REVIEW_AT else "ask"
    return Vote(decision, n / total, best[0], counted.margin, counted.support)
