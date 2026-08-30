from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

_id_counter = itertools.count(1)


def _new_id() -> str:
    return f"n{next(_id_counter)}"


class NodeState(str, enum.Enum):
    EXPLOITATION = "exploitation"  # in expanding queue, expanded first
    EXPLORATION = "exploration"  # in expanding queue, lower score
    POOLED = "pooled"  # in sorted candidate pool (N)
    EXPANDED = "expanded"  # internal node, left the frontier
    PRUNED = "pruned"  # trimmed branch (failed task)
    TERMINAL = "terminal"  # full plan formulated here


@dataclass
class Node:
    thought: str
    confidence: float
    parent_id: Optional[str] = None
    depth: int = 0
    is_terminal: bool = False

    node_id: str = field(default_factory=_new_id)
    state: NodeState = NodeState.POOLED
    children_ids: list[str] = field(default_factory=list)

    kv_ref: Any = None  # vLLM PagedAttention block ref (placeholder)
    token_sequence: list[int] = field(default_factory=list)
    target: dict[str, Any] = field(default_factory=dict)  # Target Description + Tasks
    terminal_reason: str = ""  # "llm" | "depth_cap"
    goal: str = ""
    path_steps: list[str] = field(default_factory=list)
    blocked_attempts: list[str] = field(default_factory=list)  # failed next-steps -> LLM negative constraint

    @property
    def is_root(self) -> bool:
        return self.parent_id is None
