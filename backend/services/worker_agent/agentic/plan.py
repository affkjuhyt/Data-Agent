"""The plan the model makes for one question: a DAG of steps, each a tool and what it is for.

Validated before anything runs (invariant 17): every tool exists, every dependency names an
earlier step, no cycle, within the step budget. A plan that fails validation goes back to the
planner with the reason, as a compile error goes back to the analyst pass.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_STEPS = 8

StepStatus = Literal["pending", "running", "done", "failed", "skipped"]


class PlanStep(BaseModel):
    id: str
    tool: str
    # What this step is for, in the asker's language: the executor writes the tool's arguments
    # from it and from what earlier steps found.
    instruction: str
    depends_on: list[str] = Field(default_factory=list)
    # Arguments the planner already knows; the executor may complete or correct them.
    args: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = "pending"
    observation: str = ""
    datasets: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    # The analytical goal and the reasoning behind the plan, shown on the timeline.
    goal: str
    thought: str = ""
    # "analyze": run the steps; "answer": nothing to compute (greeting, meta question about the
    # agent or the catalogue, which the reporter answers from what tools already said);
    # "ask": the question cannot be planned without the person.
    mode: Literal["analyze", "answer", "ask"] = "analyze"
    clarification: str | None = None
    options: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)


class PlanInvalid(ValueError):
    pass


def validate(plan: Plan, tools: set[str]) -> Plan:
    if plan.mode != "analyze":
        return plan
    if not plan.steps:
        raise PlanInvalid("kế hoạch không có bước nào")
    if len(plan.steps) > MAX_STEPS:
        raise PlanInvalid(f"tối đa {MAX_STEPS} bước, kế hoạch có {len(plan.steps)}")
    seen: set[str] = set()
    for step in plan.steps:
        if step.id in seen:
            raise PlanInvalid(f"bước {step.id} bị trùng mã")
        if step.tool not in tools:
            raise PlanInvalid(f"bước {step.id}: không có công cụ {step.tool}")
        for dep in step.depends_on:
            # Only an earlier step: this makes the DAG acyclic by construction.
            if dep not in seen:
                raise PlanInvalid(f"bước {step.id} phụ thuộc {dep}, không phải một bước trước nó")
        seen.add(step.id)
    return plan


def next_ready(plan: Plan) -> PlanStep | None:
    """The first pending step whose dependencies are all done; a step whose dependency failed or
    was skipped is skipped too."""
    by_id = {s.id: s for s in plan.steps}
    for step in plan.steps:
        if step.status != "pending":
            continue
        deps = [by_id[d] for d in step.depends_on if d in by_id]
        if any(d.status in ("failed", "skipped") for d in deps):
            step.status = "skipped"
            step.observation = "bỏ qua: bước phụ thuộc không thành công"
            continue
        if all(d.status == "done" for d in deps):
            return step
    return None


def view(plan: Plan) -> list[dict[str, Any]]:
    """The plan as the timeline and the plan block show it."""
    return [
        {
            "id": s.id,
            "tool": s.tool,
            "title": s.instruction,
            "status": s.status,
            "depends_on": s.depends_on,
        }
        for s in plan.steps
    ]
