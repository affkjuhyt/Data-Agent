"""A small state graph, after spring-ai-alibaba-graph's StateGraph: named async nodes over one
mutable state, plain or conditional edges, and a step budget so a loop always ends.

A node returns nothing and changes the state; an edge picks the next node from the state. The
graph is the agent's control flow written as data, so a node can be added or rerouted without
rewriting a hand-made sequence of ifs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

S = TypeVar("S")

END = "__end__"

Node = Callable[[S], Awaitable[None]]
Router = Callable[[S], str]


class GraphError(RuntimeError):
    pass


@dataclass
class StateGraph(Generic[S]):
    nodes: dict[str, Node[S]] = field(default_factory=dict)
    edges: dict[str, Router[S]] = field(default_factory=dict)
    entry: str = ""

    def node(self, name: str, fn: Node[S]) -> StateGraph[S]:
        self.nodes[name] = fn
        if not self.entry:
            self.entry = name
        return self

    def edge(self, source: str, target: str) -> StateGraph[S]:
        self.edges[source] = lambda _state: target
        return self

    def route(self, source: str, router: Router[S]) -> StateGraph[S]:
        self.edges[source] = router
        return self

    async def run(self, state: S, *, max_steps: int = 64, trail: list[str] | None = None) -> S:
        current = self.entry
        for _ in range(max_steps):
            if current == END:
                return state
            fn = self.nodes.get(current)
            if fn is None:
                raise GraphError(f"no node {current}")
            if trail is not None:
                trail.append(current)
            await fn(state)
            router = self.edges.get(current)
            current = router(state) if router is not None else END
        raise GraphError(f"the graph did not end within {max_steps} steps")
