"""Pure plan conversion and validation for the runtime boundary."""

from .validate import PlanIssue, validate_plan

__all__ = ["PlanIssue", "to_plan_ir", "validate_plan"]


def __getattr__(name: str):
    # Keep validator imports dependency-light; conversion needs the planner's
    # DPTS module, which in turn loads the optional LLM adapter.
    if name == "to_plan_ir":
        from .convert import to_plan_ir

        return to_plan_ir
    raise AttributeError(name)
