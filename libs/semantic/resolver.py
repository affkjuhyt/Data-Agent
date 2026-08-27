"""Phrasing -> metric. The half of the Semantic Layer that decides *what* was
asked for, before anything decides how to compute it.

"doanh thu", "doanh số", "revenue" and "DOANH THU" have to reach one metric id,
or the layer achieves nothing: the point is that the same question yields the
same number regardless of who typed it. Equally important is what must NOT
resolve — "doanh thu thuần" (net) is a different metric from "doanh thu" (gross)
and matching it to the shorter term would reintroduce the exact confusion the
registry exists to remove.

Deliberately deterministic, not an embedding lookup. Resolution decides which
formula produces a number a person will act on; it needs to be reproducible,
explainable ("matched synonym X"), and identical across replicas — none of which
a nearest-neighbour score over a drifting index gives you. Unmatched is a fine
outcome: the caller falls back to the existing LLM path.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from libs.semantic.models import MetricCatalog, MetricDefinition, MetricStatus

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Casefold, strip punctuation, collapse whitespace — keeping Vietnamese
    diacritics intact.

    Stripping diacritics would be actively wrong here: Vietnamese pairs that
    differ only by tone are different words, and folding them together would let
    one metric answer for another. Users who type without tones are handled by
    registering the toneless form as its own synonym — an explicit, auditable
    decision by whoever owns the metric, not a guess made in this function.
    """
    if not text:
        return ""
    # NFC first so "ố" typed as o + combining accent equals the precomposed form.
    text = unicodedata.normalize("NFC", text)
    text = _PUNCT_RE.sub(" ", text.casefold())
    return _WS_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class Resolution:
    definition: MetricDefinition
    matched_term: str
    # Longest matched synonym wins; length is kept so a caller can show why.
    matched_length: int


class MetricResolver:
    """Longest-synonym-wins matching over a tenant's catalog."""

    def __init__(self, catalog: MetricCatalog) -> None:
        self._catalog = catalog
        # Longest first: "doanh thu thuần" must be tested before "doanh thu",
        # otherwise the gross metric swallows every net question.
        self._terms = sorted(catalog.synonyms, key=len, reverse=True)

    def resolve(self, question: str) -> Resolution | None:
        """Best metric for this question, or None when nothing matches.

        None is a normal result, not an error — most questions are not about a
        registered metric, and the caller has a general-purpose path for those.
        """
        haystack = normalize(question)
        if not haystack:
            return None

        for term in self._terms:
            if not _contains_phrase(haystack, term):
                continue
            key = self._catalog.synonyms[term]
            definition = self._catalog.definitions.get(key)
            if definition is None:
                # Synonym pointing at a metric that was deleted or never loaded.
                continue
            if definition.status is MetricStatus.DEPRECATED:
                # A deprecated metric must not answer silently. Skipping lets a
                # replacement synonym match instead, and if none does the caller
                # falls back rather than serving a formula someone retired.
                continue
            return Resolution(definition=definition, matched_term=term, matched_length=len(term))
        return None


    def candidates(self, question: str) -> list[Resolution]:
        """Every metric this question could plausibly mean, best match first.

        `resolve` returns only the winner, which is right for execution and
        wrong for detecting ambiguity: when the registry holds both "doanh thu"
        and "doanh thu thuần", a question saying only "doanh thu" has one winner
        and two readings. Callers use this to ask which was meant BEFORE running
        arithmetic that differs by exactly the refunds.
        """
        haystack = normalize(question)
        if not haystack:
            return []

        found: list[Resolution] = []
        seen: set[str] = set()
        for term in self._terms:
            if not _contains_phrase(haystack, term):
                continue
            key = self._catalog.synonyms[term]
            if key in seen:
                continue
            definition = self._catalog.definitions.get(key)
            if definition is None or definition.status is MetricStatus.DEPRECATED:
                continue
            seen.add(key)
            found.append(Resolution(definition=definition, matched_term=term, matched_length=len(term)))
        return found


    def more_specific_than(self, matched_term: str) -> list[MetricDefinition]:
        """Metrics reachable by a phrase that EXTENDS `matched_term`.

        This is the shape underspecification actually takes. A user typing
        "doanh thu" matches the gross metric outright — `candidates` finds one
        winner and sees no ambiguity — while the registry also holds "doanh thu
        thuần", which the user may equally have meant. The containment runs the
        other way round: the registered term extends what was typed, not the
        reverse, so detecting it needs its own lookup rather than a second read
        of the match list.

        Returns definitions, deduplicated, excluding the metric already matched.
        """
        needle = normalize(matched_term)
        if not needle:
            return []

        prefix = f"{needle} "
        suffix = f" {needle}"
        infix = f" {needle} "
        matched_key = self._catalog.synonyms.get(needle)

        found: dict[str, MetricDefinition] = {}
        for term, key in self._catalog.synonyms.items():
            if key == matched_key or key in found or term == needle:
                continue
            # A genuine extension, on word boundaries: "doanh thu thuần" extends
            # "doanh thu"; "doanh thu" does not extend itself, and "thu" must
            # not match inside an unrelated word.
            if not (term.startswith(prefix) or term.endswith(suffix) or infix in term):
                continue
            definition = self._catalog.definitions.get(key)
            if definition is None or definition.status is MetricStatus.DEPRECATED:
                continue
            found[key] = definition
        return list(found.values())


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Whole-word phrase containment.

    Plain substring matching would fire "doanh thu" inside an unrelated longer
    token, and would match "cost" inside "costume". Both strings are already
    normalized to single-spaced words, so word boundaries are just spaces.
    """
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def build_catalog(
    definitions: list[MetricDefinition],
    synonyms: dict[str, list[str]],
) -> MetricCatalog:
    """Assemble a catalog from stored rows.

    `synonyms` maps metric key -> raw terms. The metric's own key and display
    name are registered automatically, so a metric is always reachable by its
    own name without anyone having to remember to add it as a synonym.
    """
    catalog = MetricCatalog()
    for d in definitions:
        catalog.definitions[d.key] = d

    # Sorted, NOT insertion order: on a duplicate term the first writer wins,
    # and 'first' has to mean the same thing on every replica. Iterating the
    # dict as loaded would make the winner depend on Postgres row order.
    for key in sorted(catalog.definitions):
        definition = catalog.definitions[key]
        for term in (key.replace("_", " "), definition.name, *synonyms.get(key, [])):
            normalized = normalize(term)
            if not normalized:
                continue
            existing = catalog.synonyms.get(normalized)
            if existing and existing != key:
                # Two metrics claiming one phrase is a registry bug that would
                # otherwise resolve by dict-insertion order — i.e. differently
                # on different replicas. First writer wins deterministically and
                # the clash is left for the caller to log.
                continue
            catalog.synonyms[normalized] = key
    return catalog
