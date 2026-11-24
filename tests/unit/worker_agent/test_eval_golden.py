"""EV-09's scorer: each §5.4 rubric on answers shaped as GET /v1/answers/{id} returns them, the
action check, the trust overclaim, and the report's metrics."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from model.eval import corpus
from model.eval.scorers import golden

pytestmark = [pytest.mark.unit]


def _figure(value: str, members: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "value": value,
        "dimensions": members or {},
        "display_state": "shown",
        "source": {"query_run_id": "q1", "row": 0, "col": "value"},
        "query_run_id": "q1",
        **extra,
    }


def _answer(*figures: dict[str, Any], text: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "kind": "data",
        "text": text,
        "figures": list(figures),
        "blocks": [],
        "citations": [],
        "trust_code": "GOVERNED",
        "trust": {"path": "governed"},
        **extra,
    }


def _case(**over: Any) -> dict[str, Any]:
    return {
        "id": "A-T-1",
        "question": "q",
        "group": "structured",
        "expected_action": "answer",
        "rubric": "exact",
        "verification_status": "sql_run_on_snapshot",
        "expected": {"kind": "data", "metric": "M", "values": [9554757]},
        **over,
    }


def _score(case: dict[str, Any], asked: golden.Asked, judge: Any = None) -> golden.Scored:
    return asyncio.run(golden.score_case(case, asked, judge))


# ---------------------------------------------------------------- exact


def test_exact_matches_a_figure_a_member_or_a_phrase() -> None:
    assert golden.exact([9554757], _answer(_figure("9554757")))[0]
    assert golden.exact(
        ["Midtown Center"], _answer(_figure("8240393.72", {"zone": "Midtown Center"}))
    )[0]
    assert golden.exact(["Manhattan"], _answer(text="Quận Manhattan có tỷ lệ cao nhất."))[0]
    assert not golden.exact([9554757], _answer(_figure("9554758")))[0]
    # a phrase is a whole word of the text, not a piece of one
    assert not golden.exact(["Man"], _answer(text="Manhattan"))[0]


def test_exact_rows_are_in_order() -> None:
    rows = [["A", "2.00"], ["B", "1.00"]]
    assert golden.exact(rows, _answer(_figure("2", {"z": "A"}), _figure("1", {"z": "B"})))[0]
    assert not golden.exact(rows, _answer(_figure("1", {"z": "B"}), _figure("2", {"z": "A"})))[0]


# ---------------------------------------------------------------- numeric_tolerance


def test_numeric_tolerance_is_in_percent() -> None:
    answer = _answer(_figure("79500000"))
    assert golden.numeric_tolerance(["79456198.67"], answer, 0.5)[0]
    assert not golden.numeric_tolerance(["79456198.67"], answer, 0.01)[0]


def test_numeric_tolerance_counts_a_derived_figure() -> None:
    change = _figure("-18.28", source={"compute_run_id": "c", "expr": "F2/F1-1"})
    assert golden.numeric_tolerance(["-18.27717876318522961700"], _answer(change), 0.1)[0]


def test_a_masked_figure_is_not_an_answer() -> None:
    masked = _figure("9554757", display_state="masked_stale")
    assert not golden.exact([9554757], _answer(masked))[0]


# ---------------------------------------------------------------- set_equality


def test_set_equality_in_any_order_with_unknown_members() -> None:
    rows = [["Street-hail", 153549], [None, 8455], ["Dispatch", 5576]]
    answer = _answer(
        _figure("5576", {"t": "Dispatch"}),
        _figure("8455", {"t": ""}),
        _figure("153549", {"t": "Street-hail"}),
    )
    assert golden.set_equality(rows, answer)[0]


def test_set_equality_wrong_shape_or_member_is_zero() -> None:
    rows = [["A", 1], ["B", 2]]
    assert not golden.set_equality(rows, _answer(_figure("1", {"z": "A"})))[0]
    two_cuts = _answer(_figure("1", {"z": "A", "m": "1"}), _figure("2", {"z": "B", "m": "1"}))
    assert not golden.set_equality(rows, two_cuts)[0]
    assert not golden.set_equality(
        rows, _answer(_figure("1", {"z": "A"}), _figure("2", {"z": "C"}))
    )[0]
    assert not golden.set_equality(
        rows, _answer(_figure("1", {"z": "A"}), _figure("3", {"z": "B"}))
    )[0]


def test_set_equality_leaves_a_derived_total_out() -> None:
    total = _figure("3", source={"compute_run_id": "c", "expr": "F1+F2"})
    answer = _answer(_figure("1", {"z": "A"}), _figure("2", {"z": "B"}), total)
    assert golden.set_equality([["A", 1], ["B", 2]], answer)[0]


# ---------------------------------------------------------------- contains_all


def _hybrid() -> dict[str, Any]:
    return {
        "expected_evidence": [
            {"source": "doc", "file": "data_dictionary_trip_records_yellow.pdf"},
            {"source": "query_run"},
        ],
        "values": [96699],
    }


def test_contains_all_needs_the_document_and_the_query() -> None:
    cited = [{"document_title": "data_dictionary_trip_records_yellow", "quote": "99 = Null"}]
    assert golden.contains_all(_hybrid(), _answer(_figure("96699"), citations=cited))[0]
    ok, why = golden.contains_all(_hybrid(), _answer(_figure("96699")))
    assert not ok and "data_dictionary_trip_records_yellow.pdf" in why
    no_query = _answer(_figure("96699", query_run_id=None, source={}), citations=cited)
    assert not golden.contains_all(_hybrid(), no_query)[0]


# ---------------------------------------------------------------- judge


def test_judge_needs_the_anchor_and_the_top_grade() -> None:
    case = _case(rubric="judge(tlc_why)", expected={"kind": "data", "values": ["17088580.05"]})
    answer = _answer(_figure("17088580.05"), text="Doanh thu tăng 17,1 triệu.")
    asked = golden.Asked(status="answered", answer_id="a", answer=answer)

    async def grade(value: int) -> Any:
        async def judge(*_: Any) -> int:
            return value

        return judge

    assert _score(case, asked, asyncio.run(grade(2))).passed
    assert not _score(case, asked, asyncio.run(grade(1))).passed
    unjudged = _score(case, asked)
    assert not unjudged.passed and unjudged.judged is None and "unjudged" in unjudged.detail
    wrong_anchor = golden.Asked(status="answered", answer=_answer(_figure("1")))
    assert not _score(case, wrong_anchor, asyncio.run(grade(2))).passed


def test_every_judge_rubric_of_the_cases_has_its_prompt() -> None:
    for case in corpus.load_golden("tlc"):
        kind, arg = golden.rubric_of(case["rubric"])
        if kind == "judge":
            assert arg in golden.JUDGE_PROMPTS, case["id"]


# ---------------------------------------------------------------- action and trust


def test_a_wrong_action_scores_zero_whatever_the_numbers() -> None:
    refusal = {
        "kind": "refusal",
        "text": "Không có dữ liệu",
        "figures": [],
        "trust_code": "INSUFFICIENT",
        "number_check": {"refusal": {"code": "NO_DATA_FOR_PERIOD"}},
    }
    s = _score(_case(), golden.Asked(status="refused", answer=refusal))
    assert (s.action, s.action_ok, s.passed) == ("refuse_missing_source", False, False)
    expecting = _case(expected_action="refuse_missing_source", expected={"kind": "refusal"})
    assert _score(expecting, golden.Asked(status="refused", answer=refusal)).passed


def test_access_denied_is_a_permission_refusal() -> None:
    refusal = {"kind": "refusal", "number_check": {"refusal": {"code": "ACCESS_DENIED"}}}
    assert golden.action_of(golden.Asked(status="refused", answer=refusal)) == (
        "refuse_permission",
        "ACCESS_DENIED",
    )


def test_a_clarification_and_an_error() -> None:
    clarify = _case(expected_action="clarify", expected={"kind": "clarify"})
    asked = golden.Asked(status="clarifying", answer={"kind": "clarification", "text": "?"})
    assert _score(clarify, asked).passed
    failed = _score(_case(), golden.Asked(status="error", error="ConnectError: refused"))
    assert failed.action == "error" and not failed.passed and "ConnectError" in failed.detail


def test_handover_is_an_answer_by_the_agent() -> None:
    case = _case(expected_action="handover", expected={"kind": "data", "values": [5]})
    by_agent = _answer(_figure("5"), trust={"path": "agentic"}, trust_code="EXPLORATORY")
    assert _score(case, golden.Asked(status="answered", answer=by_agent)).passed
    assert not _score(case, golden.Asked(status="answered", answer=_answer(_figure("5")))).passed


def test_a_stronger_code_than_allowed_is_an_overclaim() -> None:
    no_metric = _case(expected={"kind": "data", "values": [9554757]})
    s = _score(no_metric, golden.Asked(status="answered", answer=_answer(_figure("9554757"))))
    assert s.passed and s.expected_trust_code == "EXPLORATORY" and s.overclaim
    with_metric = _score(
        _case(), golden.Asked(status="answered", answer=_answer(_figure("9554757")))
    )
    assert not with_metric.overclaim
    weaker = _answer(_figure("9554757"), trust_code="EXPLORATORY")
    assert not _score(_case(), golden.Asked(status="answered", answer=weaker)).overclaim


def test_causal_wording_in_the_answer_is_counted() -> None:
    answer = _answer(_figure("9554757"), text="Thời tiết đẹp khiến số chuyến tăng.")
    s = _score(_case(), golden.Asked(status="answered", answer=answer))
    assert s.causal


# ---------------------------------------------------------------- the report


def test_the_report_holds_every_metric_and_row() -> None:
    cases = [
        _case(id="a"),
        _case(id="b", expected_action="clarify", group="multiturn", expected={"kind": "clarify"}),
        _case(id="c", group="hybrid", rubric="contains_all", expected={"metric": "M", **_hybrid()}),
    ]
    asked = [
        golden.Asked(
            status="answered", answer_id="x", receipt_id="r", answer=_answer(_figure("9554757"))
        ),
        golden.Asked(status="clarifying", answer_id="y", answer={"kind": "clarification"}),
        golden.Asked(status="answered", answer_id="z", answer=_answer(_figure("96699"))),
    ]
    scored = [asyncio.run(golden.score_case(c, a)) for c, a in zip(cases, asked, strict=True)]
    out = golden.report(cases, scored)
    m = out["metrics"]
    assert m["answer_correctness"] == pytest.approx(2 / 3)
    assert m["clarification_precision"] == 1.0 and m["clarification_recall"] == 1.0
    assert m["cross_source_correctness"] == 0.0 and m["citation_accuracy"] == 0.5
    assert m["receipt_share"] == 0.5 and m["trust_overclaim_rate"] == pytest.approx(1 / 3)
    row = out["results"][0]
    for name in (
        "answer_id",
        "question_id",
        "receipt_id",
        "trust_code",
        "expected_action",
        "action",
    ):
        assert name in row


def test_the_tlc_golden_set_loads_and_every_rubric_parses() -> None:
    cases = corpus.load_golden("tlc")
    assert len(cases) == 60
    for case in cases:
        golden.rubric_of(case["rubric"])
