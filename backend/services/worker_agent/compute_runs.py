"""A deterministic computation of an answer, recorded (AL-01, qa.compute_run, BR-177).

The governed path works out figures from others by arithmetic (answer/insights.py: shares,
changes, totals, the threshold and the filed-versus-current gaps), each with its expression, and
the receipt points them at one compute run. This writes that run in the answer's transaction:
which code computed them (CODE_SHA256, the source of the modules that do), from which inputs (the
query run and its result hash) and what came out (OUTPUT_SHA256 over the figures, canonical JSON
by key). A replay recomputes the figures and, when they differ and the code is no longer the code
that ran, says so (replay.py).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import uuid
from collections.abc import Sequence
from typing import Any

from libs.dataagent_db.models import qa
from services.worker_agent.answer.narrate import Figure

_ANSWER = pathlib.Path(__file__).parent / "answer"
# The modules that work out a derived figure; a change to any of them is a change of the code.
COMPUTE_MODULES = ("insights.py", "comparison.py", "thresholds.py")


def _code_sha256() -> str:
    digest = hashlib.sha256()
    for name in COMPUTE_MODULES:
        digest.update(name.encode() + b"\0" + (_ANSWER / name).read_bytes() + b"\0")
    return digest.hexdigest()


CODE_SHA256 = _code_sha256()


def output_sha256(figures: Sequence[Figure]) -> str:
    """The hash of what a computation produced: each figure's key, value and expression."""
    body = sorted(
        [f.key, None if f.value is None else format(f.value, "f"), f.expr or ""] for f in figures
    )
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


def row(
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    answer_id: uuid.UUID,
    *,
    figures: Sequence[Figure],
    inputs: list[dict[str, Any]],
    duration_ms: int,
    runtime: str = "insights",
) -> qa.ComputeRun:
    return qa.ComputeRun(
        id=run_id,
        org_id=org_id,
        answer_id=answer_id,
        runtime=runtime,
        code_sha256=CODE_SHA256,
        input_refs=inputs,
        lib_versions={},
        output_sha256=output_sha256(figures),
        output_rows=len(figures),
        duration_ms=max(0, duration_ms),
    )
