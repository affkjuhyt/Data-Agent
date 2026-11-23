"""A recorded computation (compute_runs.py, AL-01): its code and output hashes, and how a replay
tells a change of code from a change of data."""

from __future__ import annotations

import decimal
import types
import uuid

import pytest

from services.worker_agent import compute_runs, replay
from services.worker_agent.answer.narrate import Figure

pytestmark = [pytest.mark.unit]


def fig(key: str, value: str, expr: str) -> Figure:
    return types.SimpleNamespace(key=key, value=decimal.Decimal(value), expr=expr)  # type: ignore[return-value]


def test_the_output_hash_is_the_figures_whatever_their_order() -> None:
    a = [fig("F3", "1000", "F1 - F2"), fig("F4", "3000", "F1 + F2")]
    assert compute_runs.output_sha256(a) == compute_runs.output_sha256(list(reversed(a)))
    assert compute_runs.output_sha256(a) != compute_runs.output_sha256(
        [fig("F3", "1001", "F1 - F2"), fig("F4", "3000", "F1 + F2")]
    )
    assert compute_runs.output_sha256(a) != compute_runs.output_sha256(
        [fig("F3", "1000", "F2 - F1"), fig("F4", "3000", "F1 + F2")]
    )


def test_a_run_records_the_code_that_computed_it() -> None:
    assert len(compute_runs.CODE_SHA256) == 64
    run = compute_runs.row(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        figures=[fig("F3", "1", "F1 - F2")],
        inputs=[{"kind": "query_run", "id": "x", "sha256": "y"}],
        duration_ms=-3,
    )
    assert (run.runtime, run.code_sha256, run.output_rows, run.duration_ms) == (
        "insights",
        compute_runs.CODE_SHA256,
        1,
        0,
    )


def test_a_replay_blames_the_code_only_when_it_changed() -> None:
    same = types.SimpleNamespace(code_sha256=compute_runs.CODE_SHA256)
    older = types.SimpleNamespace(code_sha256="0" * 64)
    assert not replay._code_changed(same)  # type: ignore[arg-type]
    assert replay._code_changed(older)  # type: ignore[arg-type]
    assert not replay._code_changed(None)
