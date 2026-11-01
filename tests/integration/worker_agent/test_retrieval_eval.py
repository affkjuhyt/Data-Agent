"""The retrieval eval runs `retrieve` against a Data Space's indexed passages (RG-02).

Keyword half only (no vector index or reranker here): what is under test is that the harness
reaches the real pre-filter (validity at as_of) and scores what retrieval returns, not the
quality of the ranking, which needs the full stack on a cluster.
"""

from __future__ import annotations

import json

import pytest

from services.worker_agent.documents.retrieval_eval import parse_cases, run
from services.worker_agent.documents.search import SearchServices

from .conftest import Seed
from .test_document_answers import RATE, add_document, library

pytestmark = [pytest.mark.integration]

OLD_RATE = "Lãi suất huy động kỳ hạn 12 tháng là 5,2%/năm đối với khách hàng cá nhân."
CARD = "Hạn mức thẻ tín dụng doanh nghiệp do Giám đốc khối phê duyệt theo ba bước."


def _cases(*cases: dict[str, object]) -> list[str]:
    return [json.dumps(c, ensure_ascii=False) for c in cases]


async def test_the_harness_scores_what_retrieval_returns_at_each_as_of(seed: Seed) -> None:
    lib = library(seed)
    old, _ = add_document(
        seed,
        lib,
        OLD_RATE,
        doc_no="100/QĐ-NH",
        effective_from="2025-01-01",
        effective_to="2025-12-31",
    )
    add_document(seed, lib, RATE, doc_no="123/QĐ-NH", supersedes=old)
    add_document(seed, lib, CARD, title="Quy trình thẻ", doc_no="45/QT-NH")
    question = "Lãi suất huy động kỳ hạn 12 tháng cho khách hàng cá nhân là bao nhiêu?"
    cases = parse_cases(
        _cases(
            {
                "id": "now",
                "question": question,
                "as_of": "2026-09-30",
                "expect": "found",
                "gold": [{"doc_no": "123/QĐ-NH", "quote": "5,8%/năm"}],
                "forbid": [{"doc_no": "100/QĐ-NH"}],
            },
            {
                "id": "then",
                "question": question,
                "as_of": "2025-06-30",
                "expect": "found",
                "gold": [{"doc_no": "100/QĐ-NH", "quote": "5,2%/năm"}],
                "forbid": [{"doc_no": "123/QĐ-NH"}],
            },
            {
                "id": "card",
                "question": "Ai phê duyệt hạn mức thẻ tín dụng doanh nghiệp?",
                "as_of": "2026-09-30",
                "expect": "found",
                "gold": [{"doc_no": "45/QT-NH"}],
            },
            {
                "id": "absent",
                "question": "Phí chuyển tiền quốc tế bằng SWIFT là bao nhiêu?",
                "as_of": "2026-09-30",
                "expect": "not_found",
            },
        )
    )
    report = await run(lib.org, "DS-NH", cases, SearchServices(qdrant_url=None, embed_url=None))
    overall = report["overall"]
    assert report["halves"] == {"keyword": True, "dense": False, "rerank": False}
    assert overall["recall@1"] == 1.0 and overall["mrr"] == 1.0
    assert overall["not_found_accuracy"] == 1.0 and overall["forbidden_rate"] == 0.0
    assert report["misses"] == []
