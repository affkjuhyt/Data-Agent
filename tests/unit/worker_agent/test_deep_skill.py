"""Deep analysis skill activation (OP-6)."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import deep_skill as deep

pytestmark = pytest.mark.unit


def test_depth_payload_activates_skill() -> None:
    assert deep.requested({"depth": "deep"})
    assert deep.requested({"mode": "deep"})
    assert deep.requested({}, purpose="deep analysis report")
    assert not deep.requested({})
    assert not deep.requested({"depth": "quick"})


def test_inject_adds_methodology_only_when_active() -> None:
    base = "# Vai trò\nplanner"
    assert deep.inject(base, active=False) == base
    out = deep.inject(base, active=True)
    assert "phân tích sâu" in out
    assert "CẤM" in out
    assert out.startswith(base)
