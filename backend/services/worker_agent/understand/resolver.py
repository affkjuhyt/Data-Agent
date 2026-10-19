"""The Understander's rule pass (tab 5 G5.1): question → chips (metric, cut, filters, period) with a
confidence, or a clarification.

Exact matching on the normalised form (text.norm), longest phrase first, in the design's order
of priority: an approved synonym, then a dimension value from value_index, then the metric's and
dimensions' own names and codes. Fuzzy and vector matches (trigram ≥ 0.8, Qdrant ≥ 0.82) come
with the LLM path; a phrase this pass cannot place stays unmapped and is reported (BR-80).

Confidence: 1.0 for a name or code, 0.9 for a synonym. The question proceeds when the metric
chip has one meaning at ≥ 0.8 (the period may be absent: the latest data is then used and said).
Two metrics matched as well as each other → a clarification with at most three options, and no
query runs before the person picks (AC-01-01-02).
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

from services.worker_agent.understand.text import FoundPeriod, find_period, norm
from services.worker_agent.understand.value_retrieval import sem_value_retrieval, value_targets

PROCEED_AT = 0.8
MAX_OPTIONS = 3
# "theo X" asks for a cut along X ("nợ xấu theo vùng").
_BY = re.compile(r"\b(?:theo|tung|moi|chia theo)\s+")
_RANK = re.compile(
    r"\b(?:top\s*)?(\d{1,3})\b.*?\b(?:nhieu nhat|cao nhat|it nhat|thap nhat)\b"
)
# The words after "theo" that no phrase placed: up to three, before the next placed phrase.
# "theo kỳ …" ("theo kỳ được hỏi") speaks of the period, which find_period reads, not of a cut.
# Only "theo": without accents "moi" is both "mỗi" (each) and "mới" (new, "mới nhất") and "tung"
# is not only "từng", so they ask for a cut only when a dimension's name follows (_BY above).
_UNPLACED_CUT = re.compile(r"\b(?:chia )?theo\s+(?!ky\b)([^#\s]+(?:\s+[^#\s]+){0,2})")


@dataclass(frozen=True)
class MetricEntry:
    metric_id: uuid.UUID
    code: str
    name: str
    certified: bool


@dataclass(frozen=True)
class DimensionEntry:
    dimension_id: uuid.UUID
    code: str
    name: str
    is_time: bool = False


@dataclass(frozen=True)
class ValueEntry:
    dimension_code: str
    value_code: str
    label: str


@dataclass
class Vocabulary:
    """A Data Space's words: what each normalised phrase points at."""

    metrics: list[MetricEntry]
    dimensions: list[DimensionEntry]
    values: list[ValueEntry] = field(default_factory=list)
    # phrase_norm → ("metric", metric_id) | ("dimension", dimension_id) | ("value", dim_code, code)
    synonyms: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Chip:
    kind: Literal["metric", "dimension", "filter", "period"]
    value: str
    label: str
    source: Literal["default", "user_pref", "user_edit", "memory", "inherited"]
    confidence: float


@dataclass
class Understanding:
    decision: Literal["proceed", "clarify", "unknown"]
    metric: MetricEntry | None = None
    group_by: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    period: FoundPeriod | None = None
    chips: list[Chip] = field(default_factory=list)
    options: list[MetricEntry] = field(default_factory=list)  # when clarifying
    unmapped: list[str] = field(default_factory=list)
    # A cut asked for ("theo khu vực") that names no dimension: the question asks which one
    # rather than answering the total, which would answer another question (BR-80).
    unplaced_cut: str | None = None
    # A ranked request such as "top 5 sản phẩm". The compiler applies this before figures are
    # narrated, so the answer and its evidence contain exactly the requested rows.
    top_n: int | None = None
    top_desc: bool = True

    @property
    def confidence(self) -> float:
        required = [c.confidence for c in self.chips if c.kind in ("metric", "filter")]
        return min(required) if required else 0.0


def _phrases(vocab: Vocabulary) -> list[tuple[str, tuple[str, ...], float]]:
    """Every phrase this pass knows, longest first so "nợ xấu nhóm 3" beats "nợ xấu"."""
    found: dict[str, tuple[tuple[str, ...], float]] = {}

    def add(phrase: str, target: tuple[str, ...], score: float) -> None:
        key = norm(phrase)
        if key and (key not in found or found[key][1] < score):
            found[key] = (target, score)

    for m in vocab.metrics:
        add(m.name, ("metric", str(m.metric_id)), 1.0)
        add(m.code.replace("_", " "), ("metric", str(m.metric_id)), 1.0)
    for d in vocab.dimensions:
        add(d.name, ("dimension", str(d.dimension_id)), 1.0)
    for phrase, target, score in value_targets(vocab.values):
        add(phrase, target, score)
    for phrase, target in vocab.synonyms.items():
        add(phrase, target, 0.9)
    return sorted(((p, t, s) for p, (t, s) in found.items()), key=lambda x: -len(x[0]))


def understand(question: str, vocab: Vocabulary, *, today: datetime.date) -> Understanding:
    text = norm(question)
    ranking = _RANK.search(text)
    top_n = int(ranking.group(1)) if ranking else None
    period = find_period(question, today)
    if period:
        text = text.replace(period.span, " ")
    metrics = {str(m.metric_id): m for m in vocab.metrics}
    dims = {str(d.dimension_id): d for d in vocab.dimensions}
    # Value hits come from sem_value_retrieval. The phrase pass still decides where a
    # value sits against a longer metric name; a hit it did not place is not a filter.
    retrieved = {
        (hit.dimension_code, hit.value_code): hit for hit in sem_value_retrieval(text, vocab.values)
    }

    hits: list[tuple[tuple[str, ...], float, int]] = []  # target, score, position
    taken = text
    for phrase, target, score in _phrases(vocab):
        for m in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", taken):
            hits.append((target, score, m.start()))
        # Words used by a longer phrase are not matched again by a shorter one inside it.
        taken = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", lambda m: "#" * len(m.group(0)), taken)

    out = Understanding(decision="unknown", period=period)
    metric_hits: dict[str, float] = {}
    for target, score, pos in sorted(hits, key=lambda h: h[2]):
        if target[0] == "metric" and target[1] in metrics:
            metric_hits[target[1]] = max(metric_hits.get(target[1], 0.0), score)
        elif target[0] == "dimension" and target[1] in dims:
            dim = dims[target[1]]
            # A dimension named after "theo" is a cut; named alone it adds nothing to compile.
            if (_BY.search(text[max(0, pos - 12) : pos]) or top_n is not None) and dim.code not in out.group_by:
                out.group_by.append(dim.code)
                out.chips.append(Chip("dimension", dim.code, dim.name, "default", score))
        elif target[0] == "value":
            dim_code, value = target[1], target[2]
            hit = retrieved.get((dim_code, value))
            if hit is None:
                continue
            out.filters.setdefault(dim_code, [])
            if value not in out.filters[dim_code]:
                out.filters[dim_code].append(value)
                out.chips.append(Chip("filter", f"{dim_code}={value}", hit.label, "default", score))

    cut = _UNPLACED_CUT.search(taken)
    out.unplaced_cut = cut.group(1).strip() if cut else None
    leftover = " ".join(w for w in re.sub(r"#+", " ", taken).split() if len(w) > 3)
    out.unmapped = [leftover] if leftover and not metric_hits else []
    out.top_n = top_n
    out.top_desc = not bool(ranking and re.search(r"\b(?:it nhat|thap nhat)\b", ranking.group(0)))
    if period:
        out.chips.append(
            Chip("period", f"{period.start}/{period.end}", period.label, "default", 1.0)
        )

    if not metric_hits:
        return out
    best = max(metric_hits.values())
    top = [metrics[mid] for mid, score in metric_hits.items() if score == best]
    if len(top) > 1 or best < PROCEED_AT:
        out.decision = "clarify"
        out.options = sorted(top, key=lambda m: m.name)[:MAX_OPTIONS]
        return out
    out.metric = top[0]
    out.chips.insert(0, Chip("metric", top[0].code, top[0].name, "default", best))
    out.decision = "clarify" if out.unplaced_cut else "proceed"
    return out


# A quantity asked for in words ("số khoản vay", "bao nhiêu khách hàng"); "so với" and "so sánh"
# compare, they ask for no quantity of their own.
_QUANTITY = re.compile(r"\b(?:so luong|bao nhieu|so(?! voi\b| sanh\b))\s+\w")


def follow_up(
    current: Understanding,
    previous: list[dict[str, object]],
    vocab: Vocabulary,
    *,
    question: str = "",
) -> Understanding:
    """A question that only cuts, filters or dates (UC-01-04: "còn theo vùng thì sao?", "đi sâu
    Miền Bắc theo chi nhánh") read against the question before it.

    It inherits the earlier metric, and its period and filters unless it states its own; a cut
    it names replaces the earlier cut, so a drill-down is a member filter plus a new dimension.
    Inherited chips carry source `inherited` ("từ câu trước"). A question that names a metric,
    names nothing the Data Space knows, or asks for a quantity no metric holds ("Số khoản vay theo
    sản phẩm" after a question on NPL) is a new question and is returned as it was read: answering
    it with the earlier metric would answer another question.
    """
    stated = [c for c in current.chips if c.kind in ("dimension", "filter", "period")]
    if current.decision != "unknown" or current.metric is not None or not stated:
        return current
    if _QUANTITY.search(norm(question)):
        return current
    earlier = {str(c.get("kind")): c for c in previous if c.get("kind") == "metric"}
    if "metric" not in earlier:
        return current
    cut_dims = {c.value for c in current.chips if c.kind == "dimension"}
    filtered_dims = {c.value.partition("=")[0] for c in current.chips if c.kind == "filter"}
    has_period = any(c.kind == "period" for c in current.chips)

    def inherited(chip: dict[str, object]) -> dict[str, object]:
        return {**chip, "source": "inherited", "origin": None}

    def kept(chip: dict[str, object]) -> bool:
        kind = chip.get("kind")
        if kind == "dimension":
            return not cut_dims
        if kind == "filter":
            return str(chip.get("value", "")).partition("=")[0] not in filtered_dims
        return kind == "period" and not has_period

    merged: list[dict[str, object]] = [inherited(earlier["metric"])]
    merged += [inherited(chip) for chip in previous if kept(chip)]
    merged += [
        {"kind": c.kind, "value": c.value, "label": c.label, "source": c.source} for c in stated
    ]
    out = from_chips(merged, vocab)
    out.unplaced_cut = current.unplaced_cut
    if out.decision == "proceed" and out.unplaced_cut:
        out.decision = "clarify"
    return out


def from_chips(chips: list[dict[str, object]], vocab: Vocabulary) -> Understanding:
    """The question as the person corrected it (UC-01-03, PATCH /v1/questions/{id}/plan): the
    chips are taken as stated, never re-read from the text. A chip naming a metric, a dimension
    or a value the Data Space does not have makes the whole edit `unknown` rather than being
    dropped, so nothing is answered on a reading the person did not ask for."""
    out = Understanding(decision="unknown")
    metrics = {m.code: m for m in vocab.metrics}
    dims = {d.code: d for d in vocab.dimensions}
    values = {(v.dimension_code, v.value_code): v for v in vocab.values}
    for chip in chips:
        kind, value = str(chip.get("kind", "")), str(chip.get("value", ""))
        source = "user_edit" if chip.get("origin") == "user" else str(chip.get("source", "default"))
        if source not in ("default", "user_pref", "user_edit", "memory", "inherited"):
            source = "default"
        if kind == "metric":
            if value not in metrics or out.metric is not None:
                out.unmapped.append(value)
                continue
            out.metric = metrics[value]
            out.chips.append(Chip("metric", value, metrics[value].name, source, 1.0))  # type: ignore[arg-type]
        elif kind == "dimension":
            if value not in dims:
                out.unmapped.append(value)
                continue
            out.group_by.append(value)
            out.chips.append(Chip("dimension", value, dims[value].name, source, 1.0))  # type: ignore[arg-type]
        elif kind == "filter":
            dim, _, member = value.partition("=")
            if dim not in dims:
                out.unmapped.append(value)
                continue
            # A member not in value_index is still a filter the person typed; its label is theirs.
            label = (
                values[(dim, member)].label
                if (dim, member) in values
                else str(chip.get("label") or member)
            )
            out.filters.setdefault(dim, []).append(member)
            out.chips.append(Chip("filter", value, label, source, 1.0))  # type: ignore[arg-type]
        elif kind == "period":
            start, _, end = value.partition("/")
            try:
                found = FoundPeriod(
                    datetime.date.fromisoformat(start),
                    datetime.date.fromisoformat(end),
                    str(chip.get("label") or value),
                    "",
                )
            except ValueError:
                out.unmapped.append(value)
                continue
            out.period = found
            out.chips.append(Chip("period", value, found.label, source, 1.0))  # type: ignore[arg-type]
    if out.metric is not None and not out.unmapped:
        out.decision = "proceed"
    return out
