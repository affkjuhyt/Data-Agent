"""The plan the model makes for one question: a DAG of steps, each a tool and what it is for.

Validated before anything runs (invariant 17, AE-20): every tool exists (capability in the
registry), every dependency names an earlier step (no cycle), the arguments the planner already
wrote are fields of its tool (typed ports), and the steps still to run fit what the turn's budget
has left (cost). A plan that fails goes back to the planner with the reason, as a compile error
goes back to the analyst pass; PlanInvalid.error() is the structured PLANNER_ERROR the trace keeps.
Whether the asker may read a step's data is the tool's to say when it runs (`forbidden`): the
plan does not name the tables its SQL will read.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

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


Check = Literal["steps", "dag", "capability", "ports", "cost"]


class PlanInvalid(ValueError):
    def __init__(self, reason: str, *, check: Check = "steps", step: str | None = None) -> None:
        super().__init__(reason)
        self.check: Check = check
        self.step = step

    def error(self) -> dict[str, Any]:
        """The structured PLANNER_ERROR (libs/contracts/v1/errors.ErrorCode) of this failure."""
        return {
            "code": "PLANNER_ERROR",
            "check": self.check,
            "step": self.step,
            "reason": str(self),
        }


def validate(
    plan: Plan,
    tools: Set[str] | Mapping[str, type[BaseModel]],
    *,
    steps_left: int | None = None,
) -> Plan:
    """`tools` is the registry: the tool names, or each name with its arguments' model (then the
    arguments a step already holds are checked against it: a value of the wrong type fails, one
    still missing is the executor's to write). `steps_left` is what the turn's budget allows still
    to run; the steps not yet run must fit it."""
    if plan.mode != "analyze":
        return plan
    if not plan.steps:
        raise PlanInvalid("kế hoạch không có bước nào")
    if len(plan.steps) > MAX_STEPS:
        raise PlanInvalid(f"tối đa {MAX_STEPS} bước, kế hoạch có {len(plan.steps)}")
    models = tools if isinstance(tools, Mapping) else None
    seen: set[str] = set()
    for step in plan.steps:
        if step.id in seen:
            raise PlanInvalid(f"bước {step.id} bị trùng mã", check="dag", step=step.id)
        if step.tool not in tools:
            raise PlanInvalid(
                f"bước {step.id}: không có công cụ {step.tool}", check="capability", step=step.id
            )
        for dep in step.depends_on:
            # Only an earlier step: this makes the DAG acyclic by construction.
            if dep not in seen:
                raise PlanInvalid(
                    f"bước {step.id} phụ thuộc {dep}, không phải một bước trước nó",
                    check="dag",
                    step=step.id,
                )
        if models is not None and step.args and (wrong := _wrong_args(models[step.tool], step)):
            raise PlanInvalid(
                f"bước {step.id}: tham số sai kiểu cho {step.tool}: {wrong}",
                check="ports",
                step=step.id,
            )
        seen.add(step.id)
    pending = sum(1 for st in plan.steps if st.status == "pending")
    if steps_left is not None and pending > steps_left:
        raise PlanInvalid(
            f"còn {pending} bước phải chạy, ngân sách của lượt chỉ còn {max(steps_left, 0)}",
            check="cost",
        )
    return plan


def _wrong_args(model: type[BaseModel], step: PlanStep) -> str:
    try:
        model.model_validate(step.args)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(x) for x in e['loc']) or '?'}: {e['msg']}"
            for e in exc.errors()
            # A value the planner wrote, of the wrong type; not a field still to write, nor a rule
            # across fields that the executor's complete arguments may yet meet.
            if e["type"] != "missing" and e["loc"] and e["loc"][0] in step.args
        )
    return ""


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


def ready(plan: Plan, limit: int) -> list[PlanStep]:
    """Every pending step whose dependencies are done, up to `limit`: steps that need nothing of
    each other run together (gpt-researcher's parallel sub-research). A step whose dependency
    failed or was skipped is skipped, as in next_ready."""
    found: list[PlanStep] = []
    while len(found) < limit:
        step = next_ready(plan)
        if step is None:
            break
        found.append(step)
        # Held out of the next look so next_ready finds the one after it; set back below.
        step.status = "running"
    for step in found:
        step.status = "pending"
    return found


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
