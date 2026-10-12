from __future__ import annotations

import pytest
from pydantic import ValidationError

from libs.contracts.v1.plan import (
    OutputContract,
    PlanEdge,
    PlanIR,
    PlanNode,
    Port,
)

pytestmark = pytest.mark.unit


def _node(node_id: str, *, outputs: list[Port] | None = None) -> PlanNode:
    return PlanNode(
        id=node_id,
        kind="AGENT",
        capability="agent-a",
        inputs={},
        outputs=outputs or [Port(key="answer", type="text")],
    )


def test_plan_ir_uses_wire_aliases_and_round_trips() -> None:
    plan = PlanIR(
        plan_id="plan-1",
        output_contract=OutputContract(shape="text"),
        nodes=[_node("first"), _node("second")],
        edges=[PlanEdge(**{"from": "first", "to": "second"})],
        budget={"max_nodes": 3, "max_parallel": 2},
    )

    dumped = plan.model_dump()

    assert dumped["edges"] == [{"from": "first", "to": "second", "kind": "HARD_DEP"}]
    assert dumped["nodes"][0]["outputs"][0]["type"] == "text"
    assert PlanIR.model_validate(dumped) == plan


def test_plan_ir_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        _node("bad", outputs=[Port(key="answer", type="not-a-port")])
