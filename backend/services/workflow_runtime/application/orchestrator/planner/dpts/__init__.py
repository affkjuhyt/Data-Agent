"""DPTS planner components with lazy exports for lightweight model imports."""

__all__ = [
    "CandidatePool", "ExpandingQueue", "LLM", "ChildThought", "MockLLM", "node_extraction",
    "MemoryEstimator", "StaticMemoryEstimator",
    "Node", "NodeState", "Plan", "Target", "Task", "plan_formulate",
    "PlannerGate", "PlanningProcess", "Tree",
]

_MODULES = {
    "CandidatePool": ".candidate_pool", "ExpandingQueue": ".expanding_queue",
    "LLM": ".llm", "ChildThought": ".llm", "MockLLM": ".llm", "node_extraction": ".llm",
    "MemoryEstimator": ".memory", "StaticMemoryEstimator": ".memory",
    "Node": ".node", "NodeState": ".node", "Plan": ".plan", "Target": ".plan",
    "Task": ".plan", "plan_formulate": ".plan", "PlannerGate": ".planner_gate",
    "PlanningProcess": ".planning_process", "Tree": ".tree",
}


def __getattr__(name: str):
    module_name = _MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
