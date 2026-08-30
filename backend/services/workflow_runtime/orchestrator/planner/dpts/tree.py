from __future__ import annotations

from typing import Iterator, Optional

from .node import Node, NodeState


class Tree:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self.root_id: Optional[str] = None

    # node extraction -> store nodes -> Tree
    def store(self, node: Node) -> Node:
        self._nodes[node.node_id] = node
        if node.parent_id is None:
            self.root_id = node.node_id
        else:
            parent = self._nodes.get(node.parent_id)
            if parent and node.node_id not in parent.children_ids:
                parent.children_ids.append(node.node_id)
        return node

    def get(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def children(self, node_id: str) -> list[Node]:
        node = self._nodes.get(node_id)
        return [self._nodes[c] for c in node.children_ids if c in self._nodes] if node else []

    def live_children(self, node_id: str) -> list[Node]:
        return [c for c in self.children(node_id) if c.state != NodeState.PRUNED]

    def path_to_root(self, node_id: str) -> list[Node]:
        path: list[Node] = []
        cur = self._nodes.get(node_id)
        while cur is not None:
            path.append(cur)
            cur = self._nodes.get(cur.parent_id) if cur.parent_id else None
        path.reverse()
        return path

    def descendants(self, node_id: str, include_self: bool = False) -> Iterator[Node]:
        start = self._nodes.get(node_id)
        if start is None:
            return
        if include_self:
            yield start
        stack = list(start.children_ids)
        while stack:
            child = self._nodes.get(stack.pop())
            if child is None:
                continue
            yield child
            stack.extend(child.children_ids)

    # Planner gate -> trim branches -> Tree (broken node + its subtree)
    def trim_branch(self, node_id: str) -> list[str]:
        removed = [node_id] + [d.node_id for d in self.descendants(node_id)]
        for nid in removed:
            n = self._nodes.get(nid)
            if n:
                n.state = NodeState.PRUNED
        node = self._nodes.get(node_id)
        if node and node.parent_id:
            parent = self._nodes.get(node.parent_id)
            if parent:
                parent.children_ids = [c for c in parent.children_ids if c != node_id]
        return removed
