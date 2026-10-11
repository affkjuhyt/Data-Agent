from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_conversation_runner_emits_one_verification_after_evidence_only_for_delivered_answers() -> None:
    src = (
        REPO_ROOT
        / "backend/services/workflow_runtime/application/runtime_services/conversation_runner.py"
    ).read_text(encoding="utf-8")

    assert "build_verification_frame" in src
    assert "pending_verification" in src
    assert "'__evidence'" in src
    assert "'__verification'" in src
    assert src.index("'__evidence'") < src.index("'__verification'")
    assert "pending_verification = build_verification_frame" in src


def test_judge_output_is_captured_for_the_final_verification_frame() -> None:
    src = (
        REPO_ROOT
        / "backend/services/workflow_runtime/application/runtime_services/conversation_runner.py"
    ).read_text(encoding="utf-8")

    assert 'elif node == "judge":' in src
    assert 'last_verifier = out.get("verifier")' in src
