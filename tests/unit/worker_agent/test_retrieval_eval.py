"""Scoring of document retrieval against gold passages (documents/retrieval_eval.py, RG-02)."""

from __future__ import annotations

import datetime
import json
import uuid

import pytest

from services.worker_agent.documents.retrieval_eval import (
    CaseError,
    parse_cases,
    score,
    summarize,
)
from services.worker_agent.documents.retrieve import Passage
from services.worker_agent.documents.search import SearchServices

pytestmark = [pytest.mark.unit]

KEYWORD_ONLY = SearchServices(qdrant_url=None, embed_url=None)


def _line(**case: object) -> str:
    base: dict[str, object] = {
        "id": "c1",
        "question": "q",
        "as_of": "2026-09-30",
        "expect": "found",
    }
    base.update(case)
    return json.dumps(base, ensure_ascii=False)


def _passage(doc_no: str, text: str) -> Passage:
    return Passage(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        title="t",
        doc_no=doc_no,
        section_path=None,
        page_from=1,
        page_to=1,
        text=text,
        effective_from=None,
        effective_to=None,
        superseded_by=None,
        score=1.0,
    )


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (_line(gold=[]), "names its gold"),
        (_line(expect="maybe"), "expect must be"),
        (_line(expect="not_found", gold=[{"doc_no": "A"}]), "has no gold"),
        (_line(gold=[{"quote": "x"}]), "needs a doc_no"),
        (_line(gold=[{"doc_no": "A"}], as_of="30/09/2026"), "ISO date"),
    ],
)
def test_a_malformed_case_stops_the_run(line: str, why: str) -> None:
    with pytest.raises(CaseError, match=why):
        parse_cases([line])


def test_a_repeated_id_stops_the_run() -> None:
    line = _line(gold=[{"doc_no": "A"}])
    with pytest.raises(CaseError, match="repeated"):
        parse_cases([line, "", line])


def test_the_gold_quote_matches_whatever_the_chunking_and_spacing() -> None:
    [case] = parse_cases([_line(gold=[{"doc_no": "QD-1", "quote": "Không lấy  trung bình"}])])
    assert case.as_of == datetime.date(2026, 9, 30)
    passages = [
        _passage("QD-2", "Không lấy trung bình tỷ lệ."),  # right words, wrong document
        _passage("QD-1", "Tồn kho lấy số cuối kỳ."),
        _passage("QD-1", "Biên gộp:\nkhông lấy\ttrung bình tỷ lệ từng chi nhánh."),
    ]
    assert score(case, passages).rank == 3


def test_rates_over_found_absent_and_forbidden_cases() -> None:
    cases = parse_cases(
        [
            _line(id="hit1", gold=[{"doc_no": "A"}], tags=["dinh-nghia"]),
            _line(id="hit3", gold=[{"doc_no": "A"}], tags=["khong-dau"]),
            _line(id="miss", gold=[{"doc_no": "A"}], tags=["khong-dau"]),
            _line(id="absent", expect="not_found"),
            _line(id="old", gold=[{"doc_no": "A"}], forbid=[{"doc_no": "OLD"}]),
        ]
    )
    other, gold, old = _passage("B", "x"), _passage("A", "x"), _passage("OLD", "x")
    results = [
        score(cases[0], [gold]),
        score(cases[1], [other, other, gold]),
        score(cases[2], [other]),
        score(cases[3], []),
        score(cases[4], [gold, old]),
    ]
    report = summarize(results, KEYWORD_ONLY)
    overall = report["overall"]
    assert report["halves"] == {"keyword": True, "dense": False, "rerank": False}
    assert overall["recall@1"] == 0.5 and overall["recall@3"] == 0.75
    assert overall["mrr"] == round((1 + 1 / 3 + 0 + 1) / 4, 4)
    assert overall["not_found_accuracy"] == 1.0
    # Found at rank 1, but a replaced document came back with it: not a pass.
    assert overall["forbidden_rate"] == 0.2 and overall["passed"] == 3
    assert report["by_tag"]["khong-dau"]["recall@10"] == 0.5
    assert [m["id"] for m in report["misses"]] == ["miss", "old"]
