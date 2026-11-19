"""How far an answer can be trusted, as a code (AE-04, docs/blueprint/ARCHITECTURE.md §9.5).

The worker sets it, the database keeps it (qa.answer.trust_code) and the screens map it to their
own words: no screen reads a level back out of a Vietnamese label. An answer is as trusted as the
weakest of its figures and blocks.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class TrustCode(StrEnum):
    # A query a steward approved for this very question (sqlgen/verified.py).
    VERIFIED = "VERIFIED"
    # A certified metric read through the semantic compiler, inside a data product.
    GOVERNED = "GOVERNED"
    # Worked out deterministically from figures at least GOVERNED (a share, a change, a total).
    DERIVED = "DERIVED"
    # SQL a model wrote, a metric not yet certified, or one outside every data product.
    EXPLORATORY = "EXPLORATORY"
    # What the Data Space's documents say, cited passage by passage.
    DOCUMENT = "DOCUMENT"
    # Nothing to stand on: a refusal, a clarification, a figure masked or out of time.
    INSUFFICIENT = "INSUFFICIENT"


# Strongest first.
TRUST_ORDER: tuple[TrustCode, ...] = (
    TrustCode.VERIFIED,
    TrustCode.GOVERNED,
    TrustCode.DERIVED,
    TrustCode.EXPLORATORY,
    TrustCode.DOCUMENT,
    TrustCode.INSUFFICIENT,
)


def weakest(codes: Iterable[TrustCode]) -> TrustCode | None:
    """The least trusted of `codes`, None when there are none."""
    found = list(codes)
    return max(found, key=TRUST_ORDER.index) if found else None
