from __future__ import annotations

import pytest

from libs.contracts.v1.verification import VerificationFrame
from services.workflow_runtime.application.verification import build_verification_frame

pytestmark = pytest.mark.unit


def test_verification_frame_has_wire_shape_and_empty_confidence_in_r0() -> None:
    frame = VerificationFrame(
        checks=[{"name": "numbers_grounded", "passed": True, "detail": "all numbers found"}],
        verdict="PASS",
        iteration=1,
    )

    assert frame.model_dump() == {
        "checks": [{"name": "numbers_grounded", "passed": True, "detail": "all numbers found"}],
        "verdict": "PASS",
        "iteration": 1,
        "confidence": None,
    }


def test_builder_combines_deterministic_checks_with_judge_verdict() -> None:
    frame = build_verification_frame(
        {"passed": True, "checks": [{"name": "numbers_grounded", "passed": True, "detail": "ok"}]},
        judge_verdict="PASS",
        iteration=2,
    )

    assert frame == {
        "checks": [{"name": "numbers_grounded", "passed": True, "detail": "ok"}],
        "verdict": "PASS",
        "iteration": 2,
        "confidence": None,
    }


def test_builder_does_not_publish_verification_without_a_verifier_report() -> None:
    assert build_verification_frame(None, judge_verdict="PASS", iteration=0) is None
