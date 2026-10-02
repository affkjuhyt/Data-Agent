"""Four deterministic checks between an answer and the evidence behind it.

The rule this enforces is P8 of docs/v2/13-ui-principles.md: no figure reaches
a reader that is not in the evidence. The existing judge (nodes/model_harness/judge.py) asks a model
whether an answer looks grounded, which is the wrong instrument twice over — it
compares digit strings, so "1284" passes against a source saying "128,4"; and it
is a model, so the one place the system must be certain is the one place it
guesses.

Nothing here calls a model (NC-ADR-T5). Each check either points at a specific
offending token or passes, so a failure is a thing to fix rather than an opinion
to argue with, and the structured result is what the UI's VerifierBadge renders.

The four are independent on purpose. An answer can cite the right document and
still state a fabricated figure; it can state grounded figures and mix two
vintages of them; it can do both correctly and cite a page that does not exist.
Collapsing them into one score would hide which of those happened.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from libs.indicator.numeric import (
    DEFAULT_TOLERANCE,
    Reading,
    Tolerance,
    matches,
    parse,
    readings,
)

_CITATION_RE = re.compile(r"\[(\d+)\]")
# A Markdown ordered-list marker ("1. ", "2) ") at the start of a line numbers
# the answer's points; it states no figure. Counted as one, every numbered
# answer failed numbers_grounded, so the judge sent it back to the responder and
# the reader watched the answer stream, vanish and stream again.
_LIST_MARKER_RE = re.compile(r"^([ \t]*)\d{1,3}[.)](?=[ \t])", re.MULTILINE)


def _figures_text(answer: str) -> str:
    """The answer with the numbers that are not figures (citations, list markers) removed."""
    return _LIST_MARKER_RE.sub(r"\1", _CITATION_RE.sub(" ", answer))


@dataclass(frozen=True)
class EvidenceItem:
    """One figure the answer is entitled to state, and where it came from.

    Built from what the compute path already returns — an observation carries
    its release and its source_ref — so the verifier checks the same provenance
    the citation shows the reader, not a parallel record of it.
    """

    readings: tuple[Reading, ...]
    series: str | None = None
    period: str | None = None
    release_id: str | None = None
    doc_id: str | None = None
    page: int | None = None
    # What a citation of a source that is not a document opens: the query run
    # the Evidence panel shows for a warehouse result, or a web page's URL.
    source_ref: str | None = None

    @property
    def is_citable(self) -> bool:
        """Whether a `[n]` pointing here names a real document or source.

        The page is NOT required for this. Fault has to land on whoever can fix
        it: an answer that cites a marker pointing at nothing is the answer's
        mistake, while a resolved document that carries no page is the corpus's
        — those documents were ingested before coordinates were recorded, and
        failing the answer for it would make the check something to switch off
        rather than something to fix. `has_page` reports that separately.

        A warehouse result or a web page is cited too; requiring a doc_id of
        them failed every answer that cited the data it was computed from.
        """
        return bool(self.doc_id or self.source_ref)

    @property
    def has_page(self) -> bool:
        """Whether the citation can open the reader at the right place."""
        return self.page is not None


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""
    offending: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def check(self, name: str) -> Check:
        for candidate in self.checks:
            if candidate.name == name:
                return candidate
        raise KeyError(name)


def verify(
    answer: str,
    evidence: Sequence[EvidenceItem],
    tolerance: Tolerance = DEFAULT_TOLERANCE,
) -> Verdict:
    """Run all four checks. The answer passes only if every one of them does."""
    return Verdict((
        numbers_grounded(answer, evidence, tolerance),
        precision_supported(answer, evidence),
        vintage_consistent(evidence),
        citations_resolved(answer, evidence),
    ))


def _groundable(evidence: Sequence[EvidenceItem]) -> list[Reading]:
    """Every figure the evidence entitles an answer to state.

    The period is one of them. A year is a figure: "năm 2023 là 34,7%" against
    evidence for 2024 states a correct value about the wrong period, and none
    of the other three checks would notice.
    """
    pool = [reading for item in evidence for reading in item.readings]
    pool += [reading for item in evidence if item.period for reading in parse(item.period)]
    return pool


def numbers_grounded(
    answer: str, evidence: Sequence[EvidenceItem], tolerance: Tolerance = DEFAULT_TOLERANCE,
) -> Check:
    """Every figure in the answer traces to one in the evidence.

    Comparison is by VALUE at the coarser of the two written precisions, so an
    answer may round what the source published but may not restate it at a
    different magnitude.
    """
    pool = _groundable(evidence)
    stray = [
        token
        for token, candidates in readings(_figures_text(answer))
        if not matches(candidates, pool, tolerance)
    ]
    return Check(
        "numbers_grounded",
        not stray,
        "" if not stray else f"{len(stray)} figure(s) appear in no evidence item",
        tuple(stray),
    )


def precision_supported(answer: str, evidence: Sequence[EvidenceItem]) -> Check:
    """No figure is stated more precisely than the evidence published it.

    Distinct from grounding: "34,7123%" matches a source's "34,7" within
    published precision and so is grounded, while still inventing three digits
    the source never claimed. docs/v2/13-ui-principles.md section 2 wants the
    figure shown at the precision it was published at, and overstating
    certainty about a fiscal ratio is its own failure even when the value is
    right.
    """
    pool = _groundable(evidence)
    overstated = []
    for token, candidates in readings(_figures_text(answer)):
        supporting = [reading for reading in pool if matches(candidates, [reading])]
        if not supporting:
            continue  # numbers_grounded owns this failure; do not report it twice
        if min(c.decimals for c in candidates) > max(s.decimals for s in supporting):
            overstated.append(token)
    return Check(
        "precision_supported",
        not overstated,
        "" if not overstated else "figure(s) stated to more digits than the source published",
        tuple(overstated),
    )


def vintage_consistent(evidence: Sequence[EvidenceItem]) -> Check:
    """One answer reads one vintage.

    Two figures for the same series and period from different releases means a
    revised numerator was put over a stale denominator. The arithmetic is fine
    and the result is reproducible from no published source at all, which is
    the failure mode the observation store exists to make visible.
    """
    seen: dict[tuple[str, str], str] = {}
    conflicts = []
    for item in evidence:
        if not (item.series and item.period and item.release_id):
            continue
        key = (item.series, item.period)
        previous = seen.setdefault(key, item.release_id)
        if previous != item.release_id:
            conflicts.append(f"{item.series}@{item.period}")
    return Check(
        "vintage_consistent",
        not conflicts,
        "" if not conflicts else "the same series and period appear at two vintages",
        tuple(dict.fromkeys(conflicts)),
    )


def citations_resolved(answer: str, evidence: Sequence[EvidenceItem]) -> Check:
    """Every `[n]` points at an evidence item that names a real document or source.

    Markers are 1-based, as the responder prompt emits them, and they index the
    same slice of evidence the responder was shown — an off-by-one would make
    every citation point one document away from the figure it supports.

    Missing page numbers are REPORTED in `detail`, not failed; see
    `EvidenceItem.is_citable` for why those two faults belong to different
    owners.
    """
    unresolved = []
    cited: set[int] = set()
    for match in _CITATION_RE.finditer(answer):
        index = int(match.group(1))
        # Short-circuit order matters: the range test guards the index below it.
        if not 1 <= index <= len(evidence) or not evidence[index - 1].is_citable:
            unresolved.append(match.group(0))
        else:
            cited.add(index)

    # Only a document has pages; a query result or a URL has none to record.
    pageless = sum(
        1 for index in cited if evidence[index - 1].doc_id and not evidence[index - 1].has_page
    )
    if unresolved:
        detail = "citation(s) resolve to no document"
    elif pageless:
        detail = f"{pageless} citation(s) resolve to a document with no page recorded"
    else:
        detail = ""
    return Check("citations_resolved", not unresolved, detail,
                 tuple(dict.fromkeys(unresolved)))
