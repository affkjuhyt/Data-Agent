from __future__ import annotations

import pytest

from libs.contracts.v1.plan import OutputContract, PlanEdge, PlanIR, PlanNode, Port
from services.workflow_runtime.application.plan.validate import validate_plan

pytestmark = pytest.mark.unit


def _plan(
    nodes: list[PlanNode],
    edges: list[PlanEdge] | None = None,
    *,
    shape: str = "text",
    budget: dict[str, int] | None = None,
) -> PlanIR:
    return PlanIR(
        plan_id="plan-1",
        output_contract=OutputContract(shape=shape),
        nodes=nodes,
        edges=edges or [],
        budget=budget or {},
    )


def _agent(node_id: str, *, capability: str = "agent-a", depends_on: list[str] | None = None) -> PlanNode:
    return PlanNode(
        id=node_id,
        kind="AGENT",
        capability=capability,
        inputs={},
        outputs=[Port(key="answer", type="text")],
        depends_on=depends_on or [],
    )


def test_validator_accepts_independent_agent_plan() -> None:
    assert validate_plan(_plan([_agent("first")]), allowed_capabilities={"agent-a"}) == []


def test_validator_rejects_cycle() -> None:
    issues = validate_plan(
        _plan(
            [_agent("first", depends_on=["second"]), _agent("second", depends_on=["first"])],
            [PlanEdge(**{"from": "first", "to": "second"}), PlanEdge(**{"from": "second", "to": "first"})],
        ),
        allowed_capabilities={"agent-a"},
    )

    assert any(issue.code == "CYCLE" for issue in issues)


def test_validator_rejects_missing_dependency_and_unknown_capability() -> None:
    issues = validate_plan(
        _plan([_agent("first", capability="agent-unknown", depends_on=["missing"])]),
        allowed_capabilities={"agent-a"},
    )

    assert {issue.code for issue in issues} >= {"UNKNOWN_DEPENDENCY", "CAPABILITY_NOT_ALLOWED"}


def test_validator_rejects_incompatible_input_port() -> None:
    source = _agent("source")
    target = PlanNode(
        id="target",
        kind="AGENT",
        capability="agent-a",
        inputs={"context": {"from": "source", "port": "answer", "type": "result_set"}},
        outputs=[Port(key="answer", type="text")],
        depends_on=["source"],
    )

    issues = validate_plan(
        _plan([source, target], [PlanEdge(**{"from": "source", "to": "target"})]),
        allowed_capabilities={"agent-a"},
    )

    assert any(issue.code == "PORT_TYPE_MISMATCH" for issue in issues)


def test_validator_rejects_budget_and_output_shape() -> None:
    issues = validate_plan(
        _plan([_agent("first"), _agent("second")], shape="table", budget={"max_nodes": 1}),
        allowed_capabilities={"agent-a"},
        budget_limits={"max_nodes": 1},
    )

    assert {issue.code for issue in issues} >= {"BUDGET_EXCEEDED", "OUTPUT_CONTRACT_MISMATCH"}
