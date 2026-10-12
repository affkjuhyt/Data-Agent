"""Static validation for PlanIR.

This module deliberately has no database, network or runtime imports. A plan
must be safe to inspect before the executor is allowed to launch a node.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from libs.contracts.v1.plan import OutputShape, PlanIR


@dataclass(frozen=True)
class PlanIssue:
    code: str
    node_id: str | None
    path: str
    message: str


_SHAPE_PORTS: dict[OutputShape, set[str]] = {
    "table": {"result_set"},
    "series": {"result_set", "chart_spec"},
    "scalar": {"scalar"},
    "report": {"report", "text"},
    "text": {"text", "report"},
}


def validate_plan(
    plan: PlanIR,
    *,
    allowed_capabilities: Collection[str],
    budget_limits: Mapping[str, int] | None = None,
) -> list[PlanIssue]:
    """Return every static issue; an empty list means the plan is executable."""
    issues: list[PlanIssue] = []
    by_id = {node.id: node for node in plan.nodes}

    if len(by_id) != len(plan.nodes):
        issues.append(PlanIssue("DUPLICATE_NODE", None, "nodes", "node ids must be unique"))

    for node in plan.nodes:
        if node.capability not in allowed_capabilities:
            issues.append(PlanIssue(
                "CAPABILITY_NOT_ALLOWED", node.id, f"nodes[{node.id}].capability",
                f"capability {node.capability!r} is not allowed",
            ))
        for dependency in node.depends_on:
            if dependency not in by_id:
                issues.append(PlanIssue(
                    "UNKNOWN_DEPENDENCY", node.id, f"nodes[{node.id}].depends_on",
                    f"dependency {dependency!r} does not name a node",
                ))

    adjacency: dict[str, set[str]] = {node.id: set(node.depends_on) for node in plan.nodes}
    for edge in plan.edges:
        if edge.from_ not in by_id:
            issues.append(PlanIssue("UNKNOWN_EDGE_NODE", None, "edges", f"edge source {edge.from_!r} is unknown"))
        if edge.to not in by_id:
            issues.append(PlanIssue("UNKNOWN_EDGE_NODE", None, "edges", f"edge target {edge.to!r} is unknown"))
        if edge.from_ in by_id and edge.to in by_id:
            adjacency[edge.to].add(edge.from_)

    if _has_cycle(adjacency):
        issues.append(PlanIssue("CYCLE", None, "nodes", "dependencies must form a DAG"))

    outputs = {node.id: {port.key: port.type for port in node.outputs} for node in plan.nodes}
    for node in plan.nodes:
        for input_name, reference in node.inputs.items():
            if not isinstance(reference, dict) or "from" not in reference:
                continue
            source = reference["from"]
            port = reference.get("port")
            expected = reference.get("type")
            if source not in outputs:
                continue
            actual = outputs[source].get(port)
            if actual is None:
                issues.append(PlanIssue(
                    "UNKNOWN_PORT", node.id, f"nodes[{node.id}].inputs.{input_name}",
                    f"port {port!r} is not produced by {source!r}",
                ))
            elif expected is not None and actual != expected:
                issues.append(PlanIssue(
                    "PORT_TYPE_MISMATCH", node.id, f"nodes[{node.id}].inputs.{input_name}",
                    f"expected {expected!r}, received {actual!r}",
                ))

    _validate_sql_inputs(plan, issues)
    _validate_budget(plan, budget_limits, issues)
    _validate_output_contract(plan, issues)
    return issues


def _validate_sql_inputs(plan: PlanIR, issues: list[PlanIssue]) -> None:
    for node in plan.nodes:
        if node.kind != "SQL_QUERY":
            continue
        inputs = node.inputs
        analysis = inputs.get("analysis_spec")
        has_question = isinstance(inputs.get("question"), str) and bool(inputs["question"].strip())
        has_connection = isinstance(inputs.get("connection_id"), str) and bool(inputs["connection_id"].strip())
        if not isinstance(analysis, dict) and not (has_question and has_connection):
            issues.append(PlanIssue(
                "INVALID_SQL_INPUT", node.id, f"nodes[{node.id}].inputs",
                "SQL_QUERY needs a valid analysis_spec or question plus connection_id",
            ))


def _validate_budget(
    plan: PlanIR, limits: Mapping[str, int] | None, issues: list[PlanIssue]
) -> None:
    for name, value in plan.budget.items():
        if value < 0:
            issues.append(PlanIssue("INVALID_BUDGET", None, f"budget.{name}", "budget values cannot be negative"))
        if limits is not None and name in limits and value > limits[name]:
            issues.append(PlanIssue(
                "BUDGET_EXCEEDED", None, f"budget.{name}",
                f"{value} exceeds tenant limit {limits[name]}",
            ))
    max_nodes = plan.budget.get("max_nodes")
    if max_nodes is not None and len(plan.nodes) > max_nodes:
        issues.append(PlanIssue("BUDGET_EXCEEDED", None, "budget.max_nodes", "plan has more nodes than its budget"))


def _validate_output_contract(plan: PlanIR, issues: list[PlanIssue]) -> None:
    if not plan.nodes:
        issues.append(PlanIssue("EMPTY_PLAN", None, "nodes", "plan must contain at least one node"))
        return
    depended_on = {dependency for node in plan.nodes for dependency in node.depends_on}
    terminals = [node for node in plan.nodes if node.id not in depended_on]
    allowed = _SHAPE_PORTS[plan.output_contract.shape]
    if not any(port.type in allowed for node in terminals for port in node.outputs):
        issues.append(PlanIssue(
            "OUTPUT_CONTRACT_MISMATCH", terminals[-1].id if terminals else None, "output_contract.shape",
            f"terminal output does not provide {plan.output_contract.shape!r}",
        ))


def _has_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(dependency in adjacency and visit(dependency) for dependency in adjacency[node_id]):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in adjacency)
