"""The deterministic checks written with an answer (answer/verification.py, R0-8)."""

from __future__ import annotations

from typing import Any

import pytest

from libs.contracts.v1.verification import VerificationFrame
from services.worker_agent.answer.verification import verify

pytestmark = [pytest.mark.unit]

COUNTED = {"numbers_total": 2, "traced": 2, "untraceable_draft": 0, "untraceable_final": 0}


def figure(**source: Any) -> dict[str, Any]:
    return {"key": "F1", "label": "Dư nợ", "value": "3000", "source": source}


def test_a_sourced_data_answer_passes_and_matches_the_wire_contract() -> None:
    figures = [figure(query_run_id="q-1", row=0, col="v"), figure(compute_run_id="c", expr="F1")]
    out = verify("data", COUNTED, figures=figures, reconcile_badge="matched_filed")
    assert {k: out[k] for k in COUNTED} == COUNTED  # the BR-171 counts are kept as they were
    frame = VerificationFrame.model_validate(out["verification"])
    assert frame.verdict == "PASS" and frame.iteration == 1
    assert [c.name for c in frame.checks] == [
        "Số trong câu có nguồn",
        "Mỗi số có lần chạy",
        "Khớp số đã nộp",
    ]


@pytest.mark.parametrize(
    ("figures", "badge"),
    [
        ([figure(row=0, col="v")], None),  # no run behind the number
        ([figure(expr="F1 + F2")], None),  # a computation that was never run
        ([figure(query_run_id="q-1")], "differs_from_filed"),
    ],
)
def test_a_figure_without_a_run_or_off_its_filed_value_fails(
    figures: list[dict[str, Any]], badge: str | None
) -> None:
    out = verify("data", COUNTED, figures=figures, reconcile_badge=badge)
    assert out["verification"]["verdict"] == "FAIL"


def test_a_document_answer_needs_a_citation() -> None:
    cited = verify("document", COUNTED, citations=[{"n": 1}])
    assert cited["verification"]["verdict"] == "PASS"
    bare = verify("document", COUNTED, citations=[])
    assert bare["verification"]["verdict"] == "FAIL"


def test_a_refusal_states_no_number_and_carries_no_checks() -> None:
    assert "verification" not in verify("refusal", {})
    assert "verification" not in verify("clarification", {})
