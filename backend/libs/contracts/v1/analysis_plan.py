"""The analyst plan exchanged between planning, execution and chat progress."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from libs.contracts.v1.base import ContractModel, NonEmptyStr


class AnalysisStep(ContractModel):
    id: NonEmptyStr
    type: NonEmptyStr
    tool: NonEmptyStr | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    output: NonEmptyStr | None = None
    checkpoint: str = "none"
    on_error: str = "fail"


class AnalysisOutput(ContractModel):
    step: NonEmptyStr
    as_: NonEmptyStr = Field(alias="as")


class AnalysisPlan(ContractModel):
    version: int = 1
    goal: NonEmptyStr
    steps: list[AnalysisStep] = Field(min_length=1)
    outputs: list[AnalysisOutput] = Field(min_length=1)
    budget: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dag(self) -> AnalysisPlan:
        by_id = {step.id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ValueError("analysis plan step ids must be unique")
        for step in self.steps:
            if any(dep not in by_id for dep in step.depends_on):
                raise ValueError(f"analysis plan step {step.id} has an unknown dependency")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("analysis plan must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step in self.steps:
            visit(step.id)
        if any(output.step not in by_id for output in self.outputs):
            raise ValueError("analysis output references an unknown step")
        return self
