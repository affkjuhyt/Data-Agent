from __future__ import annotations

import logging

from .candidate_pool import CandidatePool
from .expanding_queue import ExpandingQueue
from .node import NodeState
from .tree import Tree

logger = logging.getLogger(__name__)


class PlannerGate:
    """Planner gate — handles a broken node reported by the executor."""

    def __init__(self, tree: Tree, pool: CandidatePool, queue: ExpandingQueue) -> None:
        self.tree = tree
        self.pool = pool
        self.queue = queue

    # broken node -> trim branches (Tree) + delete candidate associate with fail node (N);
    # re-expand parent only when it has no more child
    def report_broken_node(self, node_id: str) -> dict:
        node = self.tree.get(node_id)
        if node is None:
            raise KeyError(node_id)
        parent_id = node.parent_id or self.tree.root_id
        parent = self.tree.get(parent_id)
        if parent is None:
            raise RuntimeError("broken node has no parent")

        failed_desc = (node.target or {}).get("description") or node.thought
        if failed_desc and failed_desc not in parent.blocked_attempts:
            parent.blocked_attempts.append(failed_desc)

        removed = self.tree.trim_branch(node_id)
        # trim branches -> Tree
        self.pool.delete_associated(removed)
        # delete candidate associate with fail node -> N
        for nid in removed:
            self.queue.remove(nid)

        siblings = self.tree.live_children(parent_id)
        reexpand = not siblings
        if reexpand:
            # parent has no more child -> re-expand it
            parent.state = NodeState.POOLED
            self.pool.store(parent_id, parent.confidence)

        logger.info("planner_gate | broken=%s parent=%s trimmed=%d siblings=%d reexpand=%s",
                    node_id, parent_id, len(removed), len(siblings), reexpand)
        return {"trimmed": removed, "parent_id": parent_id, "reexpand": reexpand}
