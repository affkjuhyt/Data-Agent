"""The golden tier's scoring (EV-09, docs/blueprint/DATASET_AND_EVALUATION.md §5.4).

Each golden case (cases/golden/<dataset>/*.jsonl, EV-05) is asked through the real
POST /v1/questions (model/eval/agent_client.ask_golden) and comes back as an `Asked`: the question's
final status, the AnswerView and the receipt's id. A case is right when the system took the action
the case expects (`expected_action`) and, for an answer, its figures meet the case's `rubric`:

- `exact`: each expected value is a figure's value (as a number, equal), a figure's member or a
  phrase of the text, both after `libs.language.norm`; rows are the figures' rows, in order;
- `numeric_tolerance(pct)`: each expected value has a figure within pct percent of it;
- `set_equality`: the figures' rows (members, value) are the expected rows in any order, duplicates
  counted, each value as close as `agentic/evaluate.same_values` holds a breakdown; a row with
  more or fewer members than the case's is the wrong shape and scores 0;
- `contains_all`: every expected evidence is there (a document cited by its file, a figure read
  from a query run) and the values meet `set_equality` or a 0.5 % tolerance;
- `judge(prompt_id)`: the anchors (the expected values, worked out before) are among the figures
  and the `judge` model, another than the one answering, grades the prose with the prompt of that
  id at the top grade. Without a judge the case is reported unjudged and is not right.

A wrong action scores 0 whatever the numbers. The trust code is scored apart: a code stronger
than the case allows (TRUST_ORDER, ARCHITECTURE §9.5) is an overclaim, counted in
`trust_overclaim_rate`. What the answer says outside a hypothesis is read for causal wording,
the same sentences the runtime drops (AL-08), into `causal_claim_rate`.
"""

from __future__ import annotations

import decimal
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from model.eval import _paths

_paths.ensure()

# After _paths.ensure(): the real rules, not copies of them.
from libs.contracts.v1.trust import TRUST_ORDER, TrustCode  # noqa: E402
from libs.language import norm  # noqa: E402
from libs.language.causal import causal_sentences  # noqa: E402
from services.worker_agent.agentic.evaluate import said_of, same_values  # noqa: E402

# Only a case whose answer was checked against the snapshot is scored (§5.5); a draft is listed.
SCORED_STATUSES = frozenset({"sql_run_on_snapshot", "human_verified"})
DEFAULT_TOLERANCE_PCT = 0.5
_RUBRIC = re.compile(r"^(exact|set_equality|contains_all|numeric_tolerance|judge)(?:\((.*)\))?$")
# The paths on which the agent answered rather than the governed one (AnswerTrust.path).
HANDOVER_PATHS = frozenset({"agentic", "explore"})


@dataclass
class Asked:
    """What one golden case got from the system under test."""

    status: str  # answered | clarifying | refused | failed | cancelled | timeout | error
    question_id: str | None = None
    answer_id: str | None = None
    answer: dict[str, Any] | None = None
    receipt_id: str | None = None
    seconds: float = 0.0
    error: str | None = None


# A judge grades the prose: (prompt id, question, answer text, anchors) → 0, 1 or 2.
Judge = Callable[[str, str, str, list[Any]], Awaitable[int | None]]


@dataclass
class Scored:
    id: str
    group: str
    rubric: str
    expected_action: str
    action: str
    action_ok: bool
    values_ok: bool | None
    passed: bool
    trust_code: str | None
    expected_trust_code: str
    overclaim: bool
    path: str | None
    refusal_code: str | None
    answer_id: str | None
    question_id: str | None
    receipt_id: str | None
    seconds: float
    causal: list[str] = field(default_factory=list)
    evidence_found: int = 0
    evidence_expected: int = 0
    judged: int | None = None
    detail: str = ""


# ---------------------------------------------------------------- reading an answer


def _number(value: Any) -> decimal.Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return None


def _figures(answer: dict[str, Any], *, derived: bool) -> list[dict[str, Any]]:
    """The answer's shown figures. A total or change worked out from them (source.expr) is not a
    row of the result; it counts only where a single number is asked (`derived`)."""
    out = []
    for f in answer.get("figures") or []:
        if not isinstance(f, dict) or f.get("display_state") not in (None, "shown"):
            continue
        if not derived and "expr" in (f.get("source") or {}):
            continue
        out.append(f)
    return out


def _rows(answer: dict[str, Any]) -> list[tuple[tuple[str, ...], float]]:
    rows = []
    for f in _figures(answer, derived=False):
        value = _number(f.get("value"))
        if value is None:
            continue
        members = tuple(norm(str(m)) for m in (f.get("dimensions") or {}).values())
        rows.append((members, float(value)))
    return rows


def _members(row: Sequence[Any]) -> tuple[str, ...]:
    # A member the dictionary does not know is SQL NULL in the gold result and "" in a figure
    # (answer/narrate.figures_of).
    return tuple("" if m is None else norm(str(m)) for m in row[:-1])


def _is_row(value: Any) -> bool:
    return isinstance(value, list | tuple)


def _scalar_found(value: Any, answer: dict[str, Any]) -> bool:
    wanted = _number(value)
    figures = _figures(answer, derived=True)
    if wanted is not None and any(_number(f.get("value")) == wanted for f in figures):
        return True
    phrase = norm(str(value))
    if not phrase:
        return False
    members = {norm(str(m)) for f in figures for m in (f.get("dimensions") or {}).values()}
    if phrase in members:
        return True
    return (
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm(answer.get("text") or "")) is not None
    )


def _within(value: Any, answer: dict[str, Any], pct: float) -> bool:
    wanted = _number(value)
    if wanted is None:
        return False
    bound = abs(wanted) * decimal.Decimal(str(pct)) / 100
    return any(
        (got := _number(f.get("value"))) is not None and abs(got - wanted) <= bound
        for f in _figures(answer, derived=True)
    )


# ---------------------------------------------------------------- rubrics


def exact(values: Sequence[Any], answer: dict[str, Any]) -> tuple[bool, str]:
    if values and all(_is_row(v) for v in values):
        got = [(m, _number(v)) for m, v in _rows_exact(answer)]
        want = [(_members(r), _number(r[-1])) for r in values]
        ok = got == want
        return ok, "" if ok else f"rows {len(got)} in order, expected {len(want)}"
    missing = [v for v in values if not _scalar_found(v, answer)]
    return not missing, f"not found: {missing}" if missing else ""


def _rows_exact(answer: dict[str, Any]) -> list[tuple[tuple[str, ...], Any]]:
    return [
        (tuple(norm(str(m)) for m in (f.get("dimensions") or {}).values()), f.get("value"))
        for f in _figures(answer, derived=False)
        if f.get("value") is not None
    ]


def numeric_tolerance(
    values: Sequence[Any], answer: dict[str, Any], pct: float
) -> tuple[bool, str]:
    if values and all(_is_row(v) for v in values):
        return set_equality(values, answer, pct / 100)
    missing = [v for v in values if not _within(v, answer, pct)]
    return not missing, f"none within {pct}% of {missing}" if missing else ""


def set_equality(
    values: Sequence[Any], answer: dict[str, Any], tolerance: float | None = None
) -> tuple[bool, str]:
    """The expected rows in any order: the same members, each member's values the same multiset
    (`same_values`, duplicates counted)."""
    if not values or not all(_is_row(v) for v in values):
        # A single value asked as a set: the figures' values are that multiset.
        got_values = [v for _, v in _rows(answer)]
        want_values = [float(v) for v in values if _number(v) is not None]
        ok = same_values(got_values, want_values, *(() if tolerance is None else (tolerance,)))
        return ok, "" if ok else f"values {sorted(got_values)} vs {sorted(want_values)}"
    got_rows = _rows(answer)
    width = {len(r) - 1 for r in values}
    if any(len(m) not in width for m, _ in got_rows) or len(got_rows) != len(values):
        return False, f"shape: {len(got_rows)} rows, expected {len(values)}"
    got: dict[tuple[str, ...], list[float]] = defaultdict(list)
    want: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for members, value in got_rows:
        got[members].append(value)
    for row in values:
        number = _number(row[-1])
        want[_members(row)].append(float(number) if number is not None else float("nan"))
    if set(got) != set(want):
        return False, f"members {sorted(set(got) ^ set(want))[:5]} differ"
    args = () if tolerance is None else (tolerance,)
    wrong = [m for m in want if not same_values(got[m], want[m], *args)]
    return not wrong, f"values differ for {wrong[:5]}" if wrong else ""


def evidence(expected: list[dict[str, Any]], answer: dict[str, Any]) -> tuple[int, list[str]]:
    """How many of the expected evidence the answer carries, and what is missing."""
    citations = [c for c in answer.get("citations") or [] if isinstance(c, dict)]
    figures = _figures(answer, derived=True)
    found, missing = 0, []
    for item in expected:
        if item.get("source") == "doc":
            stem = norm(str(item.get("file", "")).rsplit(".", 1)[0])
            ok = bool(stem) and any(
                stem in norm(str(c.get(k) or "")) or norm(str(c.get(k) or "")) in stem
                for c in citations
                for k in ("document_title", "doc_no", "file_name")
                if c.get(k)
            )
        elif item.get("source") == "query_run":
            ok = any(
                f.get("query_run_id") or (f.get("source") or {}).get("query_run_id")
                for f in figures
            )
        else:
            ok = False
        found += ok
        if not ok:
            missing.append(str(item.get("file") or item.get("source")))
    return found, missing


def contains_all(expected: dict[str, Any], answer: dict[str, Any]) -> tuple[bool, str]:
    found, missing = evidence(expected.get("expected_evidence") or [], answer)
    values = expected.get("values") or []
    if values and all(_is_row(v) for v in values):
        values_ok, why = set_equality(values, answer)
    else:
        values_ok, why = numeric_tolerance(values, answer, DEFAULT_TOLERANCE_PCT)
    detail = "; ".join(x for x in (f"missing evidence: {missing}" if missing else "", why) if x)
    return not missing and values_ok, detail


# ---------------------------------------------------------------- judge prompts

# Each prompt is graded 0/1/2 against anchors worked out before the run (the gold SQL's result),
# so the judge checks the prose against numbers rather than its own idea of them; a number the
# answer states and the data does not give costs the grade (DAComp).
JUDGE_PROMPTS: dict[str, str] = {
    "tlc_why": (
        "Bạn chấm câu trả lời cho một câu hỏi 'vì sao' về dữ liệu taxi NYC. Mốc đúng (tính trước "
        "từ dữ liệu): {anchors}.\n"
        "Điểm 2: nêu đúng mức chênh lệch theo mốc, tách được phần đóng góp từ dữ liệu, và không "
        "khẳng định nguyên nhân ngoài dữ liệu (thời tiết, sự kiện) trừ khi ghi rõ là giả thuyết.\n"
        "Điểm 1: mức chênh đúng nhưng không tách đóng góp, hoặc có một câu suy đoán không ghi là "
        "giả thuyết.\n"
        "Điểm 0: sai mức chênh, có số không có trong dữ liệu, hoặc khẳng định nguyên nhân.\n"
        "Câu hỏi: {question}\nCâu trả lời: {answer}\n"
        'Chỉ trả về JSON {{"score": 0|1|2, "reason": "..."}}.'
    ),
}


# ---------------------------------------------------------------- the case


def rubric_of(text: str) -> tuple[str, str | None]:
    found = _RUBRIC.match(text.strip())
    if not found:
        raise ValueError(f"unknown rubric {text!r}")
    return found.group(1), found.group(2)


def action_of(asked: Asked) -> tuple[str, str | None]:
    """The action the system took, and the refusal's code when it refused."""
    answer = asked.answer or {}
    kind = answer.get("kind")
    if asked.status in ("failed", "cancelled", "timeout", "error") and kind is None:
        return "error", None
    if kind == "clarification" or asked.status == "clarifying":
        return "clarify", None
    if kind == "refusal" or asked.status == "refused":
        check = answer.get("number_check") if isinstance(answer.get("number_check"), dict) else {}
        code = ((check or {}).get("refusal") or {}).get("code")
        return ("refuse_permission" if code == "ACCESS_DENIED" else "refuse_missing_source"), code
    return "answer", None


def action_ok(expected: str, action: str, path: str | None) -> bool:
    if expected == "handover":
        return action == "answer" and path in HANDOVER_PATHS
    # No answer marks a conflict between two sources yet: a flag_conflict case cannot pass until
    # one does, which is what the case is there to show.
    if expected == "flag_conflict":
        return False
    return action == expected


def expected_trust(case: dict[str, Any]) -> TrustCode:
    """The strongest code the case allows: its own, or what its action and evidence imply."""
    expected = case.get("expected") or {}
    code = case.get("expected_trust_code") or expected.get("expected_trust_code")
    if code:
        return TrustCode(code)
    action = case.get("expected_action")
    if action in ("clarify", "refuse_missing_source", "refuse_permission"):
        return TrustCode.INSUFFICIENT
    if action == "handover":
        return TrustCode.EXPLORATORY
    cites_doc = any(e.get("source") == "doc" for e in expected.get("expected_evidence") or [])
    if cites_doc:
        return TrustCode.DOCUMENT
    # A certified metric read through the compiler is GOVERNED; anything else the model wrote.
    return TrustCode.GOVERNED if expected.get("metric") else TrustCode.EXPLORATORY


def overclaims(code: str | None, allowed: TrustCode) -> bool:
    if code is None:
        return False
    return TRUST_ORDER.index(TrustCode(code)) < TRUST_ORDER.index(allowed)


async def score_case(case: dict[str, Any], asked: Asked, judge: Judge | None = None) -> Scored:
    expected = case.get("expected") or {}
    answer = asked.answer or {}
    trust = answer.get("trust") if isinstance(answer.get("trust"), dict) else {}
    path = (trust or {}).get("path")
    action, code = action_of(asked)
    acted = action_ok(case["expected_action"], action, path)
    values_ok: bool | None = None
    detail = asked.error or ""
    judged: int | None = None
    found, wanted = 0, len(expected.get("expected_evidence") or [])
    if case["expected_action"] in ("answer", "handover", "flag_conflict") and action == "answer":
        kind, arg = rubric_of(case["rubric"])
        values = expected.get("values") or []
        if kind == "exact":
            values_ok, why = exact(values, answer)
        elif kind == "numeric_tolerance":
            values_ok, why = numeric_tolerance(values, answer, float(arg or DEFAULT_TOLERANCE_PCT))
        elif kind == "set_equality":
            values_ok, why = set_equality(values, answer)
        elif kind == "contains_all":
            values_ok, why = contains_all(expected, answer)
        else:
            anchors_ok, why = numeric_tolerance(values, answer, DEFAULT_TOLERANCE_PCT)
            if judge is not None and arg in JUDGE_PROMPTS:
                judged = await judge(arg, case["question"], said_of(_view(answer)), values)
            values_ok = anchors_ok and judged == 2
            why = "; ".join(x for x in (why, f"judge {judged}" if judge else "unjudged") if x)
        if wanted:
            found, _ = evidence(expected["expected_evidence"], answer)
        detail = "; ".join(x for x in (detail, why) if x)
    passed = (
        acted
        and (values_ok is not False)
        and (
            values_ok is True
            or case["expected_action"] not in ("answer", "handover", "flag_conflict")
        )
    )
    allowed = expected_trust(case)
    said = (
        said_of(_view(answer))
        if answer.get("kind") not in (None, "refusal", "clarification")
        else ""
    )
    return Scored(
        id=case["id"],
        group=case.get("group", ""),
        rubric=case["rubric"],
        expected_action=case["expected_action"],
        action=action,
        action_ok=acted,
        values_ok=values_ok,
        passed=passed,
        trust_code=answer.get("trust_code"),
        expected_trust_code=str(allowed),
        overclaim=overclaims(answer.get("trust_code"), allowed),
        path=path,
        refusal_code=code,
        answer_id=asked.answer_id,
        question_id=asked.question_id,
        receipt_id=asked.receipt_id,
        seconds=round(asked.seconds, 2),
        causal=causal_sentences(said) if said.strip() else [],
        evidence_found=found,
        evidence_expected=wanted,
        judged=judged,
        detail=detail[:300],
    )


def _view(answer: dict[str, Any]) -> Any:
    # said_of reads an ORM answer's text and blocks; the API's AnswerView has the same two.
    return SimpleNamespace(text=answer.get("text") or "", blocks=answer.get("blocks") or [])


# ---------------------------------------------------------------- the report


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


GROUP_METRICS = {
    "structured": "answer_correctness_structured",
    "multiturn": "answer_correctness_multiturn",
    "analytical": "numeric_correctness",
    "hybrid": "cross_source_correctness",
}


def report(cases: Sequence[dict[str, Any]], scored: Sequence[Scored]) -> dict[str, Any]:
    """The §6.2 metrics of a golden run, and every case's row. A rate with nothing to count is
    None, so the gate reads it as absent rather than as a perfect score."""
    n = len(scored)
    clarified = [s for s in scored if s.action == "clarify"]
    should = [s for s in scored if s.expected_action == "clarify"]
    answered = [s for s in scored if s.action == "answer"]
    with_text = [s for s in answered if s.trust_code is not None]
    by_group: dict[str, list[Scored]] = defaultdict(list)
    for s in scored:
        by_group[s.group].append(s)
    metrics: dict[str, float | None] = {
        "answer_correctness": _rate(sum(s.passed for s in scored), n),
        "action_accuracy": _rate(sum(s.action_ok for s in scored), n),
        "clarification_precision": _rate(
            sum(s.expected_action == "clarify" for s in clarified), len(clarified)
        ),
        "clarification_recall": _rate(sum(s.action == "clarify" for s in should), len(should)),
        "governed_share": _rate(sum(s.trust_code in ("VERIFIED", "GOVERNED") for s in scored), n),
        "trust_overclaim_rate": _rate(sum(s.overclaim for s in scored), n),
        "causal_claim_rate": _rate(sum(bool(s.causal) for s in with_text), len(with_text)),
        "citation_accuracy": _rate(
            sum(s.evidence_found for s in scored), sum(s.evidence_expected for s in scored)
        ),
        "receipt_share": _rate(sum(bool(s.receipt_id) for s in answered), len(answered)),
    }
    for group, name in GROUP_METRICS.items():
        if by_group.get(group):
            metrics[name] = _rate(sum(s.passed for s in by_group[group]), len(by_group[group]))
    return {
        "cases": n,
        "skipped": [c["id"] for c in cases if c.get("verification_status") not in SCORED_STATUSES],
        "errors": sum(s.action == "error" for s in scored),
        "unjudged": [s.id for s in scored if s.rubric.startswith("judge") and s.judged is None],
        "by_group": {
            g: {"cases": len(v), "passed": sum(s.passed for s in v)}
            for g, v in sorted(by_group.items())
        },
        "results": [s.__dict__ for s in scored],
        "metrics": {k: v for k, v in metrics.items() if v is not None},
    }
