from __future__ import annotations

from typing import Optional

from .memory import MemoryEstimator, StaticMemoryEstimator


class ExpandingQueue:
    """Queue Q = exploitation + exploration (flexible size |P|)."""

    def __init__(
            self,
            estimator: Optional[MemoryEstimator] = None,
            exploit_ratio: float = 0.5,
            min_exploit: int = 1,
            min_explore: int = 1,
    ) -> None:
        self.estimator = estimator or StaticMemoryEstimator(4)
        self.exploit_ratio = exploit_ratio
        self.min_exploit = min_exploit
        self.min_explore = min_explore
        self.exploitation: dict[str, float] = {}  # id -> confidence
        self.exploration: dict[str, float] = {}

    @property
    def capacity(self) -> int:
        return self.estimator.queue_size()

    @property
    def max_exploitation(self) -> int:
        n = int(round(self.capacity * self.exploit_ratio))
        return max(self.min_exploit, min(n, self.capacity - self.min_explore))

    @property
    def max_exploration(self) -> int:
        return max(self.min_explore, self.capacity - self.max_exploitation)

    def has_exploitation_slot(self) -> bool:
        return len(self.exploitation) < self.max_exploitation

    def has_exploration_slot(self) -> bool:
        return len(self.exploration) < self.max_exploration

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.exploitation or node_id in self.exploration

    def add_exploitation(self, node_id: str, confidence: float) -> None:
        self.exploration.pop(node_id, None)
        self.exploitation[node_id] = confidence

    def add_exploration(self, node_id: str, confidence: float) -> None:
        self.exploitation.pop(node_id, None)
        self.exploration[node_id] = confidence

    def remove(self, node_id: str) -> bool:
        hit = node_id in self
        self.exploitation.pop(node_id, None)
        self.exploration.pop(node_id, None)
        return hit

    # lowest-confidence exploitation node (promotion target to dequeue)
    def weakest_exploitation(self) -> Optional[tuple[str, float]]:
        if not self.exploitation:
            return None
        nid = min(self.exploitation, key=lambda k: self.exploitation[k])
        return nid, self.exploitation[nid]

    def all_ids(self) -> set[str]:
        return set(self.exploitation) | set(self.exploration)
