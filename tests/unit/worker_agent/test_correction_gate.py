"""Correction memory steward gate (OP-7)."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import correction_gate as cg

pytestmark = pytest.mark.unit


def test_escalate_only_feedback_at_threshold() -> None:
    assert not cg.should_escalate(kind="feedback", times=2)
    assert cg.should_escalate(kind="feedback", times=3)
    assert cg.should_escalate(kind="feedback", times=5)
    assert not cg.should_escalate(kind="preference", times=5)
    assert not cg.should_escalate(kind="definition", times=3)


def test_proposal_diff_documents_why_and_how() -> None:
    shape = cg.proposal_diff_shape()
    assert "why" in shape and "how_to_apply" in shape
