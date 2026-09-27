from __future__ import annotations

import heapq
import itertools
from typing import Iterable, Optional


class CandidatePool:
    """sorted candidate pool by node confidence score (N)."""

    def __init__(self) -> None:
        self._open: list[tuple[float, int, str]] = []  # lazy-deletion max-heap of non-terminal ids
        self._open_members: set[str] = set()
        self._terminals: dict[str, float] = {}  # store current terminal nodes
        self._seq = itertools.count()

    def __len__(self) -> int:
        return len(self._open_members) + len(self._terminals)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._open_members or node_id in self._terminals

    # node extraction -> store nodes (non-terminal) into N
    def store(self, node_id: str, confidence: float) -> None:
        if node_id in self._open_members:
            return
        self._open_members.add(node_id)
        heapq.heappush(self._open, (-confidence, next(self._seq), node_id))

    # node extraction -> store current terminal nodes into N
    def store_terminal(self, node_id: str, confidence: float) -> None:
        self._terminals[node_id] = confidence

    # expanding queue <- N : top confident open node
    def pop_best_open(self) -> Optional[str]:
        while self._open and self._open[0][2] not in self._open_members:
            heapq.heappop(self._open)
        if not self._open:
            return None
        node_id = heapq.heappop(self._open)[2]
        self._open_members.discard(node_id)
        return node_id


    # pop terminal node : highest-confidence terminal candidate plan
    def pop_terminal(self, exclude: set[str] | None = None) -> Optional[str]:
        exclude = exclude or set()
        best, best_conf = None, -1.0
        for nid, conf in self._terminals.items():
            if nid not in exclude and conf > best_conf:
                best, best_conf = nid, conf
        return best

    def remove(self, node_id: str) -> bool:
        hit = node_id in self._open_members or node_id in self._terminals
        self._open_members.discard(node_id)
        self._terminals.pop(node_id, None)
        return hit

    # Planner gate -> branch fail: delete candidate associate with fail node
    def delete_associated(self, node_ids: Iterable[str]) -> list[str]:
        return [nid for nid in node_ids if self.remove(nid)]

    def terminal_ids(self) -> list[str]:
        return list(self._terminals)

    def open_ids(self) -> list[str]:
        return list(self._open_members)
