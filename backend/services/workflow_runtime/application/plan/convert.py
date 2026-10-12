from __future__ import annotations

from libs.contracts.v1.plan import OutputContract, PlanEdge, PlanIR, PlanNode, Port
from services.workflow_runtime.application.orchestrator.planner.dpts.plan import Plan


def to_plan_ir(plan: Plan) -> PlanIR:
    """Translate the existing DPTS plan without changing executor semantics."""
    nodes: list[PlanNode] = []
    edges: list[PlanEdge] = []
    for target in plan.targets:
        task_ids = {index: f"{target.node_id}:{index}" for index in range(len(target.tasks))}
        for index, task in enumerate(target.tasks):
            dependencies: list[str] = []
            for raw_dependency in task.depend_on:
                try:
                    dependency_index = int(raw_dependency)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"task dependency {raw_dependency!r} is not an index") from exc
                if dependency_index not in task_ids:
                    raise ValueError(f"task dependency {dependency_index} is outside target {target.node_id}")
                dependencies.append(task_ids[dependency_index])
                edges.append(PlanEdge(**{"from": task_ids[dependency_index], "to": task_ids[index]}))
            nodes.append(PlanNode(
                id=task_ids[index],
                kind="AGENT",
                capability=task.agent_id,
                inputs={"prompt": task.input, "title": task.title},
                outputs=[Port(key="answer", type="text")],
                depends_on=dependencies,
            ))
    return PlanIR(
        plan_id=plan.plan_id,
        output_contract=OutputContract(shape="text"),
        nodes=nodes,
        edges=edges,
    )
