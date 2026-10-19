"""AC-03-01-07's scorer: it catches a contribution worded as a cause and lets the method's
own disclaimer through, and the standard "vì sao" set passes the gate."""

from __future__ import annotations

import pytest

from model.eval import corpus
from model.eval.scorers import causal

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    "text",
    [
        "SME gây ra mức tăng của tỷ lệ nợ xấu.",
        "Nguyên nhân là nhóm SME tăng nợ xấu.",
        "Miền Bắc dẫn đến tỷ lệ cao hơn.",
        "Lãi suất tăng khiến nợ xấu tăng.",
        "Nợ xấu tăng bởi vì SME.",
    ],
)
def test_a_contribution_worded_as_a_cause_is_caught(text: str) -> None:
    assert causal.causal_sentences(text) == [text]


def test_the_arithmetic_and_its_disclaimer_are_not_claims() -> None:
    text = (
        "Trong đó +0,34 điểm % do tỷ lệ trong từng nhóm thay đổi. "
        "Đây là phần đóng góp theo vùng, không phải nguyên nhân; muốn kết luận nguyên nhân cần "
        "kiểm định riêng."
    )
    assert causal.causal_sentences(text) == []


@pytest.mark.req("AC-03-01-07")
def test_the_standard_why_set_concludes_no_cause_and_carries_its_method() -> None:
    report = causal.score(corpus.load_cases("why"))
    assert report["cases"] >= 12
    assert report["metrics"] == {
        "causal_claim_rate": 0.0,
        "why_method_coverage": 1.0,
        "why_decomposition_accuracy": 1.0,
    }, report


def test_a_distinct_count_is_right_only_when_refused() -> None:
    """Contributions of a distinct count do not add up to its change (one customer counted in
    two channels is one customer in the total), so the skill must refuse to decompose it."""
    measures = {"so_kh": "COUNT(DISTINCT ma_kh)", "du_no": "SUM(du_no)"}
    case = {
        "id": "t",
        "expression": "so_kh",
        "measures": measures,
        "metric": "Số khách hàng",
        "dimension": "kênh",
        "base_label": "08/2026",
        "current_label": "09/2026",
        "base": {"Online": [100], "Quầy": [200]},
        "current": {"Online": [180], "Quầy": [210]},
        "expect": {"refused": True},
    }
    assert causal.score([case])["metrics"]["why_decomposition_accuracy"] == 1.0
    answered = {**case, "expression": "du_no", "metric": "Dư nợ"}
    assert causal.score([answered])["metrics"]["why_decomposition_accuracy"] == 0.0
