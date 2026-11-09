"""Blast-radius disposition matrix (OP-3): capability x autonomy → auto | hitl | forbid."""

from __future__ import annotations

import pytest

from services.worker_agent import blast_radius as br

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("level", "autonomy", "checkpoints", "requires", "over_budget", "expected"),
    [
        ("read", "propose_approve", [], False, False, "auto"),
        ("send_external", "act_then_report", [], False, False, "hitl"),
        ("write_derived", "act_then_report", [], False, False, "auto"),
        ("write_derived", "act_then_report", ["before_write_derived"], False, False, "hitl"),
        ("write_derived", "propose_approve", [], True, False, "hitl"),
        ("write_derived", "propose_approve", [], False, False, "auto"),
        ("write_derived", "suggest_only", [], False, False, "hitl"),
        ("read", "propose_approve", [], False, True, "hitl"),
        ("write_source", "act_then_report", [], False, False, "forbid"),
    ],
)
def test_disposition_matrix(
    level: str,
    autonomy: str,
    checkpoints: list[str],
    requires: bool,
    over_budget: bool,
    expected: str,
) -> None:
    assert (
        br.disposition(
            level,
            autonomy=autonomy,
            checkpoints=checkpoints,
            requires_checkpoint=requires,
            over_budget=over_budget,
        )
        == expected
    )


def test_write_source_raises_on_needs_checkpoint() -> None:
    with pytest.raises(ValueError, match="outside product scope"):
        br.needs_checkpoint(
            "write_source",
            autonomy="act_then_report",
            checkpoints=[],
            requires_checkpoint=False,
        )
