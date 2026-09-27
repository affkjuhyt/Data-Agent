"""Locale-aware numeric parsing and tolerance comparison.

Shared by the release gate's scorers and by the runtime verifier, in
`backend/libs` rather than in `model/eval` for the reason that file's Dockerfile
gives about every other rule it measures: an eval that vendors its own copy
grades the copy. "Is this figure grounded" has to have one answer, whether it is
asked of a live answer or of a golden case.

Where `quantity.py` does arithmetic on figures whose unit and scale are already
known, this module goes the other way — it reads figures OUT of text a model
wrote, where the convention is ambiguous and the writer is not trusted.

The scorers used to compare figures as digit strings with separators removed,
so `128,4` and `1284` were the same token. For BI that was a tolerable
shortcut; for fiscal figures it means an answer can restate a source's
"128,4 tỷ" as "1284" and still be scored as grounded — a wrong number wearing
a correct number's clothes. Everything here exists to compare VALUES instead.

Two decisions carry the design:

**Ambiguity is represented, not guessed.** Vietnamese writes 3.109,2 where
English writes 3,109.2, and a lone separator followed by exactly three digits
(`12.720`) is genuinely both readings — 12720 grouped, or 12.720 fractional.
Rather than pick one and be silently wrong, `parse` returns every reading a
token admits; a comparison succeeds when ANY reading matches. Tokens that are
NOT ambiguous stay strict, which is the whole point: `128,4` has one digit
after the separator, cannot be a thousands group, and so never reads as 1284.

**Precision comes from the figures, not from a constant.** Comparing at the
coarser of the two published precisions is what docs/v2/06-evaluation-plan.md
section 4 calls for in its first row — an answer of "2,5%" against a computed
2,5157% is correct at the precision it was stated, while a single global epsilon
is either too loose for `3.109,2` or too tight for `2,5`. Cases that compute a
figure rather than quote one declare an explicit Tolerance instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

# A run of digits that may carry group/decimal separators. Trailing separators
# are punctuation ("...đạt 128,4." ) and get trimmed before parsing.
_TOKEN_RE = re.compile(r"\d[\d.,]*")


@dataclass(frozen=True)
class Reading:
    """One way to read a token: its value, and how precisely it was written.

    `decimals` is the count of digits after the decimal separator as the source
    wrote them, which is what "published precision" means when comparing.
    """

    value: Decimal
    decimals: int


@dataclass(frozen=True)
class Tolerance:
    """How far apart two figures may be and still count as the same figure.

    `published_precision` (the default) compares after rounding both sides to
    the coarser of their two written precisions. `absolute` and `relative` are
    for cases whose expected value is computed rather than quoted, where
    rounding of the inputs propagates — docs/v2/06-evaluation-plan.md section 4, row 2.
    """

    kind: Literal["published_precision", "absolute", "relative"] = "published_precision"
    value: Decimal = Decimal(0)


DEFAULT_TOLERANCE = Tolerance()


def parse(token: str) -> tuple[Reading, ...]:
    """Every value `token` can denote, most likely reading first.

    Returns an empty tuple for anything that is not a number, so callers can
    treat "unparseable" and "no reading" the same way.
    """
    token = token.strip().strip(".,")
    if not token or not token[0].isdigit():
        return ()

    dots = token.count(".")
    commas = token.count(",")

    if dots and commas:
        # Whichever separator comes last is the decimal point; the other groups
        # digits. True for both conventions, so no locale flag is needed.
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        return _single(token, decimal_sep)

    sep = "." if dots else "," if commas else ""
    if not sep:
        return _readings(token, "")

    if token.count(sep) > 1:
        # 1.234.567 — repeated separators only ever group.
        return _readings(token.replace(sep, ""), "")

    tail = token.rsplit(sep, 1)[1]
    if len(tail) == 3:
        # The ambiguous shape. Integer reading first: in a Vietnamese corpus a
        # lone "12.720" is far more often twelve thousand than twelve-point-
        # seven-two-zero, but both are offered so neither is silently wrong.
        grouped = _readings(token.replace(sep, ""), "")
        fractional = _single(token, sep)
        return grouped + fractional
    return _single(token, sep)


def _single(token: str, decimal_sep: str) -> tuple[Reading, ...]:
    group_sep = "," if decimal_sep == "." else "."
    return _readings(token.replace(group_sep, "").replace(decimal_sep, "."), ".")


def _readings(cleaned: str, decimal_sep: str) -> tuple[Reading, ...]:
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return ()
    decimals = len(cleaned.rsplit(decimal_sep, 1)[1]) if decimal_sep and decimal_sep in cleaned else 0
    return (Reading(value, decimals),)


def readings(text: str) -> list[tuple[str, tuple[Reading, ...]]]:
    """Every numeric token in `text`, paired with the values it can denote.

    The original token is kept alongside so reports can quote what the answer
    actually said rather than a normalised form the reader never wrote.
    """
    out = []
    for token in _TOKEN_RE.findall(text):
        parsed = parse(token)
        if parsed:
            out.append((token, parsed))
    return out


def close(a: Reading, b: Reading, tolerance: Tolerance = DEFAULT_TOLERANCE) -> bool:
    """Whether two readings denote the same figure under `tolerance`."""
    if tolerance.kind == "absolute":
        return abs(a.value - b.value) <= tolerance.value
    if tolerance.kind == "relative":
        scale = max(abs(a.value), abs(b.value))
        return abs(a.value - b.value) <= tolerance.value * scale
    places = min(a.decimals, b.decimals)
    return _round(a.value, places) == _round(b.value, places)


def _round(value: Decimal, places: int) -> Decimal:
    # ROUND_HALF_UP, not the Decimal default of ROUND_HALF_EVEN: published
    # figures are rounded the way people round, and banker's rounding would
    # disagree with the source on exactly the .5 boundary the tolerance exists
    # to cover.
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def matches(
    candidates: tuple[Reading, ...],
    supported: list[Reading],
    tolerance: Tolerance = DEFAULT_TOLERANCE,
) -> bool:
    """Whether any reading of a token matches any supported figure."""
    return any(close(c, s, tolerance) for c in candidates for s in supported)
