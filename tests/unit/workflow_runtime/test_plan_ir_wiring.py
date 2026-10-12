from __future__ import annotations

import pytest

from services.workflow_runtime.application.orchestrator.planner.dpts.plan import Plan, Target, Task
from services.workflow_runtime.application.plan.convert import to_plan_ir

pytestmark = pytest.mark.unit


def test_dpts_tasks_become_agent_nodes_with_dependency_edges() -> None:
    plan = Plan(
        plan_id="p-1",
        user_input="compare sales",
        targets=[Target(
            node_id="target-1",
            description="collect and compare",
            tasks=[
                Task(title="collect", input="sales", agent_id="agent-a"),
                Task(title="compare", agent_id="agent-b", depend_on=[0]),
            ],
        )],
    )

    ir = to_plan_ir(plan)

    assert [node.id for node in ir.nodes] == ["target-1:0", "target-1:1"]
    assert ir.nodes[1].depends_on == ["target-1:0"]
    assert ir.edges[0].model_dump() == {"from": "target-1:0", "to": "target-1:1", "kind": "HARD_DEP"}
    assert ir.output_contract.shape == "text"


def test_conversion_rejects_task_dependency_outside_target() -> None:
    plan = Plan(
        plan_id="p-1",
        user_input="q",
        targets=[Target(
            node_id="target-1",
            description="bad",
            tasks=[Task(title="bad", agent_id="agent-a", depend_on=[4])],
        )],
    )

    with pytest.raises(ValueError, match="dependency"):
        to_plan_ir(plan)
