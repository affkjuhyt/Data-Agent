"""Pure-function tests for backend/services/workflow_runtime/workflow_memory.py —
no Docker, no network, no LLM. Covers the recency*duration scoring formula
(doc §3.7/§IV), which is plain Python with no I/O.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_workflow_score_prefers_recent_run_over_older_one_at_equal_relevance():
    import workflow_memory as wm

    now = datetime.now(timezone.utc)
    recent = {"created_at": now - timedelta(days=1), "duration_ms": 1000}
    older = {"created_at": now - timedelta(days=60), "duration_ms": 1000}

    assert wm._workflow_score(recent, relevance=0.9) > wm._workflow_score(older, relevance=0.9)


@pytest.mark.unit
def test_workflow_score_prefers_faster_run_at_equal_recency_and_relevance():
    import workflow_memory as wm

    now = datetime.now(timezone.utc)
    fast = {"created_at": now, "duration_ms": 200}
    slow = {"created_at": now, "duration_ms": 20000}

    assert wm._workflow_score(fast, relevance=0.8) > wm._workflow_score(slow, relevance=0.8)


@pytest.mark.unit
def test_workflow_score_zero_relevance_is_zero_regardless_of_recency_or_duration():
    import workflow_memory as wm

    row = {"created_at": datetime.now(timezone.utc), "duration_ms": 100}
    assert wm._workflow_score(row, relevance=0.0) == 0.0


@pytest.mark.unit
def test_workflow_score_missing_duration_means_no_penalty():
    """duration_ms=None (e.g. a tool that didn't report timing) must not be
    scored as if it were a slow run — inverse_duration should be 1.0, the
    same as duration_ms=0, not the half-life value at duration_ms=_DURATION_NORMALIZER_MS."""
    import workflow_memory as wm

    now = datetime.now(timezone.utc)
    row_no_duration = {"created_at": now, "duration_ms": None}
    row_zero_duration = {"created_at": now, "duration_ms": 0}
    row_at_halflife = {"created_at": now, "duration_ms": wm._DURATION_NORMALIZER_MS}

    score_no_duration = wm._workflow_score(row_no_duration, relevance=0.5)
    score_zero_duration = wm._workflow_score(row_zero_duration, relevance=0.5)
    score_at_halflife = wm._workflow_score(row_at_halflife, relevance=0.5)

    assert score_no_duration == pytest.approx(score_zero_duration)
    assert score_no_duration > score_at_halflife
    assert score_at_halflife == pytest.approx(score_no_duration * 0.5)
