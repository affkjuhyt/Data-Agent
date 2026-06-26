from __future__ import annotations

from typing import TYPE_CHECKING, Union

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from .node import Node


class Task(BaseModel):
    title: str = ""
    input: str = ""   # nội dung cần xử lý (chỉ dùng cho task độc lập, planner extract từ query)
    agent_id: str = ""
    depend_on: list[Union[int, str]] = Field(default_factory=list)

    @field_validator("depend_on", mode="before")
    @classmethod
    def _coerce(cls, v):
        if v is None:
            return []
        if isinstance(v, (int, str)):
            return [v]
        if isinstance(v, list):
            return [x for x in v if isinstance(x, (int, str))]
        return []


class Target(BaseModel):
    node_id: str = ""                # the Tree node — report this on a task failure
    description: str
    tasks: list[Task] = Field(default_factory=list)


class Plan(BaseModel):
    plan_id: str
    user_input: str  # plan stored with the user query
    targets: list[Target] = Field(default_factory=list)
    node_path: list[str] = Field(default_factory=list)  # provenance back into the Tree
    terminal_node_id: str = ""
    confidence: float = 0.0


def _to_target(raw: dict) -> Target:
    tasks = [t if isinstance(t, Task) else Task(**t) for t in raw.get("tasks", [])]
    return Target(description=raw.get("description", ""), tasks=tasks)


# terminal node -> Plan Formulate -> Plan (root->terminal path of Targets)
def plan_formulate(plan_id: str, user_input: str, path: list["Node"]) -> Plan:
    targets: list[Target] = []
    confs: list[float] = []
    for node in path:
        confs.append(node.confidence)
        if not node.target:
            continue
        t = _to_target(node.target) if isinstance(node.target, dict) else node.target
        if not t.tasks:                     # a plan step must have executable tasks; skip "plan complete" markers
            continue
        t.node_id = node.node_id            # executor reports this node_id if a task here breaks
        targets.append(t)
    scored = confs[1:] if len(confs) > 1 else confs
    agg = sum(scored) / len(scored) if scored else 0.0
    terminal = path[-1] if path else None
    return Plan(
        plan_id=plan_id,
        user_input=user_input,
        targets=targets,
        node_path=[n.node_id for n in path],
        terminal_node_id=terminal.node_id if terminal else "",
        confidence=round(agg, 4),
    )
