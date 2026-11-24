"""How far a confidence can be trusted, and the map that makes it a probability (F-31, EV-19).

docs/blueprint/CONFIDENCE_SPEC.md §2. A producer (the reading of a question, the SQL vote) gives
each turn a raw score `x`; an eval set says which turns were right. From those pairs:

* `fit` makes a `CalibrationMap`: x is cut into ordered bins (each distinct value its own bin
  when there are few, equal-mass bins otherwise), each bin's accuracy is smoothed towards the
  producer's overall accuracy by a Beta prior of strength `PRIOR_STRENGTH`, the bins are made
  non-decreasing in x by pool-adjacent-violators, and each pooled block gets a one-sided Wilson
  lower bound. Decisions read that bound (`p_low`), so a bin of three turns is never trusted as
  much as one of thirty.
* `ece`, `brier`, `risk_coverage`, `aurc` say how good a set of probabilities is, and
  `cross_fit` gives the out-of-fold probabilities they are measured on: fitted and measured on
  the same turns a map always looks better than it is.
* `choose_thresholds` picks the lowest bars that keep the error among answered turns within an
  accepted risk, on the Wilson upper bound of that error.

Standard library only: the worker applies a map, the eval harness (model/eval/calibrate.py) fits
one, and neither needs numpy for it.
"""

from __future__ import annotations

import json
import math
import random
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# One-sided 95 %: the bound a decision reads.
Z = 1.645
PRIOR_STRENGTH = 4.0
# Equal-mass bins of a continuous score; ECE's bins on the probabilities.
FEW_TURNS = 200


def bin_count(n: int) -> int:
    """5 bins under FEW_TURNS turns, 10 from there: fewer turns per bin than that is noise."""
    return 5 if n < FEW_TURNS else 10


def wilson(successes: float, n: float, z: float = Z) -> tuple[float, float]:
    """Wilson score interval of a proportion; (0, 1) when there is nothing to go on."""
    if n <= 0:
        return 0.0, 1.0
    p = min(max(successes / n, 0.0), 1.0)
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)


def pava(values: Sequence[float], weights: Sequence[float]) -> list[tuple[int, int, float, float]]:
    """Pool adjacent violators: the non-decreasing fit of `values` in least squares, as blocks
    (first index, last index, pooled value, pooled weight)."""
    blocks: list[list[float]] = []  # [first, last, value, weight]
    for i, (v, w) in enumerate(zip(values, weights, strict=True)):
        blocks.append([i, i, v, w])
        while len(blocks) > 1 and blocks[-2][2] > blocks[-1][2]:
            last = blocks.pop()
            prev = blocks[-1]
            weight = prev[3] + last[3]
            prev[2] = (prev[2] * prev[3] + last[2] * last[3]) / weight if weight else prev[2]
            prev[1], prev[3] = last[1], weight
    return [(int(b[0]), int(b[1]), b[2], b[3]) for b in blocks]


def edges(xs: Sequence[float], max_bins: int) -> list[tuple[float, float]]:
    """Ordered [lo, hi] ranges of x. A value never spans two bins: ties stay together."""
    counts: dict[float, int] = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    distinct = sorted(counts)
    if len(distinct) <= max_bins:
        return [(x, x) for x in distinct]
    target = len(xs) / max_bins
    out: list[tuple[float, float]] = []
    current: list[float] = []
    filled = 0
    for x in distinct:
        current.append(x)
        filled += counts[x]
        if filled >= target and len(out) < max_bins - 1:
            out.append((current[0], current[-1]))
            current, filled = [], 0
    if current:
        out.append((current[0], current[-1]))
    return out


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int
    correct: int
    p: float  # smoothed, pooled to be non-decreasing in x
    p_low: float  # Wilson lower bound of the pooled block


@dataclass(frozen=True)
class CalibrationMap:
    producer: str
    bins: tuple[Bin, ...]
    prior_a: float
    prior_b: float
    version: str = "unversioned"

    def apply(self, x: float) -> tuple[float, float]:
        """(p, p_low) for a raw score. Between two bins, the lower one; under the lowest, the
        lowest: a value the eval set never showed is not trusted more than its weaker neighbour."""
        if not self.bins:
            prior = self.prior_a / (self.prior_a + self.prior_b)
            return prior, wilson(self.prior_a, self.prior_a + self.prior_b)[0]
        at = max(0, bisect_right([b.lo for b in self.bins], x) - 1)
        b = self.bins[at]
        return b.p, b.p_low

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "version": self.version,
            "prior": {"a0": self.prior_a, "b0": self.prior_b},
            "bins": [
                {
                    "lo": b.lo,
                    "hi": b.hi,
                    "n": b.n,
                    "correct": b.correct,
                    "p": b.p,
                    "p_low": b.p_low,
                }
                for b in self.bins
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationMap:
        return cls(
            producer=str(data["producer"]),
            version=str(data.get("version") or "unversioned"),
            prior_a=float(data["prior"]["a0"]),
            prior_b=float(data["prior"]["b0"]),
            bins=tuple(
                Bin(
                    float(b["lo"]),
                    float(b["hi"]),
                    int(b["n"]),
                    int(b["correct"]),
                    float(b["p"]),
                    float(b["p_low"]),
                )
                for b in data["bins"]
            ),
        )


def fit(
    xs: Sequence[float],
    ys: Sequence[bool],
    producer: str,
    *,
    max_bins: int | None = None,
    prior_strength: float = PRIOR_STRENGTH,
    version: str = "unversioned",
) -> CalibrationMap:
    """The map of a producer from its turns (x, right or not). CONFIDENCE_SPEC §2.2."""
    if len(xs) != len(ys):
        raise ValueError("xs and ys differ in length")
    n = len(xs)
    # The overall accuracy, itself smoothed so that an eval set with no wrong turn does not
    # make the prior certain.
    base = (sum(ys) + 1) / (n + 2)
    a0, b0 = prior_strength * base, prior_strength * (1 - base)
    if not n:
        return CalibrationMap(producer, (), a0, b0, version)
    ranges = edges(xs, max_bins or bin_count(n))
    counts = [[0, 0] for _ in ranges]  # [n, correct]
    los = [lo for lo, _ in ranges]
    for x, y in zip(xs, ys, strict=True):
        at = max(0, bisect_right(los, x) - 1)
        counts[at][0] += 1
        counts[at][1] += int(y)
    smoothed = [(c + a0) / (k + prior_strength) for k, c in counts]
    weights = [k + prior_strength for k, _ in counts]
    bins: list[Bin] = []
    for first, last, value, weight in pava(smoothed, weights):
        low = wilson(value * weight, weight)[0]
        for i in range(first, last + 1):
            lo, hi = ranges[i]
            bins.append(Bin(lo, hi, counts[i][0], counts[i][1], value, low))
    return CalibrationMap(producer, tuple(bins), a0, b0, version)


def brier(ps: Sequence[float], ys: Sequence[bool]) -> float:
    return sum((p - float(y)) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps) if ps else 0.0


def ece(ps: Sequence[float], ys: Sequence[bool], n_bins: int | None = None) -> float:
    """Expected calibration error over equal-mass bins of the probabilities (sorted, then cut
    into nearly equal chunks: ties may fall on both sides of a cut, as is usual for it)."""
    n = len(ps)
    if not n:
        return 0.0
    k = min(n_bins or bin_count(n), n)
    order = sorted(range(n), key=lambda i: ps[i])
    total = 0.0
    for b in range(k):
        chunk = order[b * n // k : (b + 1) * n // k]
        if chunk:
            accuracy = sum(ys[i] for i in chunk) / len(chunk)
            confidence = sum(ps[i] for i in chunk) / len(chunk)
            total += len(chunk) / n * abs(accuracy - confidence)
    return total


@dataclass(frozen=True)
class Operating:
    threshold: float
    coverage: float  # share of all turns at or above the threshold
    answered: int
    errors: int
    risk: float  # errors / answered
    risk_high: float  # Wilson upper bound of the risk


def risk_coverage(ps: Sequence[float], ys: Sequence[bool]) -> list[Operating]:
    """One operating point per distinct probability, highest first."""
    n = len(ps)
    out: list[Operating] = []
    answered = errors = 0
    pairs = sorted(zip(ps, ys, strict=True), key=lambda t: -t[0])
    i = 0
    while i < n:
        threshold = pairs[i][0]
        while i < n and pairs[i][0] == threshold:
            answered += 1
            errors += int(not pairs[i][1])
            i += 1
        out.append(
            Operating(
                threshold,
                answered / n,
                answered,
                errors,
                errors / answered,
                wilson(errors, answered)[1],
            )
        )
    return out


def aurc(ps: Sequence[float], ys: Sequence[bool]) -> float:
    """Area under the risk-coverage curve, as steps: lower is better."""
    area, covered = 0.0, 0.0
    for point in risk_coverage(ps, ys):
        area += point.risk * (point.coverage - covered)
        covered = point.coverage
    return area


def band(ps: Sequence[float], ys: Sequence[bool], lo: float, hi: float = math.inf) -> Operating:
    """The turns with lo ≤ p < hi, as one operating point (coverage is their share of all)."""
    inside = [y for p, y in zip(ps, ys, strict=True) if lo <= p < hi]
    errors = sum(not y for y in inside)
    k = len(inside)
    return Operating(
        lo,
        k / len(ps) if ps else 0.0,
        k,
        errors,
        errors / k if k else 0.0,
        wilson(errors, k)[1],
    )


def choose_thresholds(
    scores: Sequence[float], ys: Sequence[bool], r_answer: float, r_review: float
) -> tuple[float | None, float | None]:
    """(τ_answer, τ_review): the lowest bars whose answered turns keep the Wilson upper bound of
    their error within r_answer, and those of the band under τ_answer within r_review. None when
    no bar does: nothing may be answered (or reviewed) on this evidence. CONFIDENCE_SPEC §2.4."""
    candidates = sorted(set(scores), reverse=True)
    tau_answer = None
    for t in candidates:
        if band(scores, ys, t).risk_high <= r_answer:
            tau_answer = t
    ceiling = tau_answer if tau_answer is not None else math.inf
    tau_review = None
    for t in candidates:
        if t >= ceiling:
            continue
        b = band(scores, ys, t, ceiling)
        if b.answered and b.risk_high <= r_review:
            tau_review = t
    return tau_answer, tau_review


def cross_fit(
    xs: Sequence[float],
    ys: Sequence[bool],
    producer: str,
    *,
    folds: int = 5,
    seed: int = 0,
    max_bins: int | None = None,
) -> tuple[list[float], list[float]]:
    """Out-of-fold (p, p_low) of every turn: each fold is predicted by a map fitted on the
    others. Deterministic for a seed."""
    n = len(xs)
    k = max(2, min(folds, n))
    order = list(range(n))
    random.Random(seed).shuffle(order)
    fold_of = {i: rank % k for rank, i in enumerate(order)}
    ps, lows = [0.0] * n, [0.0] * n
    for f in range(k):
        train = [i for i in range(n) if fold_of[i] != f]
        m = fit(
            [xs[i] for i in train],
            [ys[i] for i in train],
            producer,
            max_bins=max_bins or bin_count(n),
        )
        for i in range(n):
            if fold_of[i] == f:
                ps[i], lows[i] = m.apply(xs[i])
    return ps, lows


def bootstrap(
    statistic: Callable[[Sequence[float], Sequence[bool]], float],
    ps: Sequence[float],
    ys: Sequence[bool],
    *,
    rounds: int = 1000,
    seed: int = 0,
) -> tuple[float, float]:
    """95 % percentile interval of a statistic of (p, y) pairs, resampled with replacement."""
    n = len(ps)
    if not n:
        return 0.0, 0.0
    rng = random.Random(seed)
    values = []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        values.append(statistic([ps[i] for i in idx], [ys[i] for i in idx]))
    values.sort()
    return values[int(0.025 * rounds)], values[min(rounds - 1, int(0.975 * rounds))]


# CONFIDENCE_SPEC §4 (AE-23): the SQL vote counted by way of writing. A way of writing no better
# than a coin (a_m ≤ 0.5, logit ≤ 0) still casts a small vote, never a negative one.
MIN_WEIGHT = 0.05
WEIGHTS_FILE = Path(__file__).parent / "maps" / "sqlgen_vote.json"


def method_weight(correct: int, items: int) -> float:
    """w_m = logit(a_m), a_m = (correct + 1) / (items + 2): the weight of an independent voter
    right a_m of the time in an optimal majority vote (one-parameter Dawid-Skene), floored."""
    a = (correct + 1) / (items + 2)
    return max(math.log(a / (1 - a)), MIN_WEIGHT)


@dataclass(frozen=True)
class MethodVote:
    winner: int | None  # the first candidate of the winning group
    margin: float  # (S1 - S2) / W: how far the winner is ahead, of every way's weight
    support: dict[str, float]  # each way of writing's share of its samples in the winner


def method_vote(
    groups: Sequence[str | None],
    methods: Sequence[str],
    weights: Mapping[str, float] | None = None,
) -> MethodVote:
    """The vote of §4 over candidates: `groups[i]` is candidate i's result (None when it did not
    run), `methods[i]` its way of writing. Each way gives its weight to the groups its samples
    fell in, split evenly between them (two samples of one way in two groups: half each), so
    ways of writing vote, not samples; W counts every way written, the failed ones too. Ties go
    to the group seen first, as the count vote did. No weights: each way weighs 1."""
    samples: dict[str, int] = {}
    for m in methods:
        samples[m] = samples.get(m, 0) + 1
    known = list((weights or {}).values())
    fallback = sum(known) / len(known) if known else 1.0
    weight = {m: (weights or {}).get(m, fallback) for m in samples}
    total = sum(weight.values())
    first: dict[str, int] = {}
    score: dict[str, float] = {}
    for i, (g, m) in enumerate(zip(groups, methods, strict=True)):
        if g is None:
            continue
        first.setdefault(g, i)
        score[g] = score.get(g, 0.0) + weight[m] / samples[m]
    if not score:
        return MethodVote(None, 0.0, {})
    # Rounded so that halves adding up the same way in another order still tie.
    ranked = sorted(score, key=lambda g: (-round(score[g], 9), first[g]))
    best = ranked[0]
    runner_up = score[ranked[1]] if len(ranked) > 1 else 0.0
    support = {
        m: sum(1 for g, mm in zip(groups, methods, strict=True) if mm == m and g == best) / n
        for m, n in samples.items()
    }
    margin = (score[best] - runner_up) / total if total > 0 else 0.0
    return MethodVote(first[best], margin, support)


def method_weights() -> dict[str, float] | None:
    """The weights of the ways of writing fitted on the last ablation (maps/sqlgen_vote.json),
    or None when there are none: the vote then weighs each way 1."""
    try:
        data = json.loads(WEIGHTS_FILE.read_text())
    except (OSError, ValueError):
        return None
    found = data.get("method_weights") if isinstance(data, dict) else None
    if not isinstance(found, dict) or not found:
        return None
    return {str(k): float(v) for k, v in found.items()}
