from __future__ import annotations

from typing import Any, Callable
import asyncio
import logging
import uuid

from .candidate_pool import CandidatePool
from .expanding_queue import ExpandingQueue
from .llm import LLM, MockLLM, node_extraction
from .memory import MemoryEstimator
from .node import Node, NodeState
from .plan import Plan, plan_formulate
from .planner_gate import PlannerGate
from .tree import Tree

logger = logging.getLogger(__name__)

_STATE_VERSION = 1


def _node_to_dict(n: Node) -> dict[str, Any]:
    return {
        "node_id": n.node_id,
        "thought": n.thought,
        "confidence": n.confidence,
        "parent_id": n.parent_id,
        "depth": n.depth,
        "is_terminal": n.is_terminal,
        "state": n.state.value,
        "children_ids": list(n.children_ids),
        "target": n.target,
        "terminal_reason": n.terminal_reason,
        "goal": n.goal,
        "path_steps": list(n.path_steps),
        "blocked_attempts": list(n.blocked_attempts),
    }


def _node_from_dict(d: dict[str, Any]) -> Node:
    return Node(
        thought=d["thought"],
        confidence=d["confidence"],
        parent_id=d.get("parent_id"),
        depth=d.get("depth", 0),
        is_terminal=d.get("is_terminal", False),
        node_id=d["node_id"],
        state=NodeState(d.get("state", "pooled")),
        children_ids=list(d.get("children_ids", [])),
        target=d.get("target") or {},
        terminal_reason=d.get("terminal_reason", ""),
        goal=d.get("goal", ""),
        path_steps=list(d.get("path_steps", [])),
        blocked_attempts=list(d.get("blocked_attempts", [])),
    )


class PlanningProcess:
    """Main workflow: LLM -> node extraction -> Tree/N -> expanding queue ->
    terminal node -> plan exist -> Plan Formulate. Planner gate handles failures."""

    def __init__(self, llm: LLM | MockLLM, *, h_es: float = 0.5, h_ds: float = 0.7,
                 branching: int = 3, estimator: MemoryEstimator | None = None,
                 exploit_ratio: float = 0.5, max_plan_depth: int = 4,
                 max_layers: int = 64,
                 on_layer: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.llm = llm
        self.h_es = h_es
        self.h_ds = h_ds
        self.branching = branching
        self.max_plan_depth = max_plan_depth
        self.max_layers = max_layers
        self.on_layer = on_layer

        self.tree = Tree()
        self.pool = CandidatePool()
        self.queue = ExpandingQueue(estimator=estimator, exploit_ratio=exploit_ratio)
        self.gate = PlannerGate(self.tree, self.pool, self.queue)

        self.user_input = ""
        self.agents: list[dict[str, Any]] = []   # published agents injected before planning
        self.produced: set[str] = set()
        self.layers_run = 0

    # ── public workflow ────────────────────────────────────────────────
    async def plan(self, user_input: str, agents: list[dict[str, Any]] | None = None) -> Plan | None:
        self.agents = agents or []
        await self.seed(user_input)
        return await self.run_until_terminal()

    async def resume(self) -> Plan | None:
        # previous plan fail expand signal
        return await self.run_until_terminal()

    def report_broken_node(self, node_id: str) -> dict[str, Any]:
        # executor -> Planner gate
        result = self.gate.report_broken_node(node_id)
        self.produced -= set(result["trimmed"])
        return result

    # ── seed ────────────────────────────────────────────────────────────
    async def seed(self, user_input: str) -> None:
        self.user_input = user_input
        root = Node(thought=user_input, confidence=1.0, parent_id=None, depth=0,
                    state=NodeState.EXPANDED, goal=user_input, path_steps=[])
        self.tree.store(root)
        children = await self._generate(root)
        # LLM -> node extraction -> store nodes (Tree)
        self._route_to_pool(root, children)
        # store nodes / terminal nodes -> N
        self._refill_queue()

    # ── expansion loop ──────────────────────────────────────────────────
    async def run_until_terminal(self) -> Plan | None:
        terminal = self.pool.pop_terminal(exclude=self.produced)  # plan exist?
        if terminal:
            return self._formulate(terminal)
        for _ in range(self.max_layers):
            self._refill_queue()
            if not self.queue.all_ids():
                self._emit()
                return None
            await self._expand_layer()
            self._refill_queue()
            self._emit()
            terminal = self.pool.pop_terminal(exclude=self.produced)
            if terminal:
                # increase plan counter if terminal
                return self._formulate(terminal)
        return None

    # exploitation considered first, then exploration; generation batched per layer
    async def _expand_layer(self) -> None:
        exploit = list(self.queue.exploitation)
        explore = list(self.queue.exploration)
        nodes = [n for n in (self.tree.get(i) for i in exploit + explore) if n is not None]
        results = await asyncio.gather(*(self._generate(n) for n in nodes))
        children = {n.node_id: c for n, c in zip(nodes, results)}
        for nid in exploit:
            self._exploitation_update(nid, children.get(nid, []))
        for nid in explore:
            self._exploration_update(nid, children.get(nid, []))
        self.layers_run += 1

    # best child >= h_es -> inherits exploitation slot, others -> N; else slot freed (refill from N)
    def _exploitation_update(self, parent_id: str, children: list[Node]) -> None:
        parent = self.tree.get(parent_id)
        self.queue.remove(parent_id)
        if parent:
            parent.state = NodeState.EXPANDED
        non_terminal = self._route_terminals(parent, children)
        if not non_terminal:
            return
        best = max(non_terminal, key=lambda c: c.confidence)
        if best.confidence >= self.h_es:
            self.queue.add_exploitation(best.node_id, best.confidence)
            best.state = NodeState.EXPLOITATION
            for c in non_terminal:
                if c.node_id != best.node_id:
                    self._pool(c)
        else:
            for c in non_terminal:
                self._pool(c)

    # exploration child > h_ds -> promote to exploitation (swap weakest if full); else -> N
    def _exploration_update(self, parent_id: str, children: list[Node]) -> None:
        parent = self.tree.get(parent_id)
        self.queue.remove(parent_id)
        if parent:
            parent.state = NodeState.EXPANDED
        for c in self._route_terminals(parent, children):
            if c.confidence > self.h_ds:
                self._promote(c)
            else:
                self._pool(c)

    def _promote(self, child: Node) -> None:
        if self.queue.has_exploitation_slot():
            self.queue.add_exploitation(child.node_id, child.confidence)
            child.state = NodeState.EXPLOITATION
            return
        weak = self.queue.weakest_exploitation()
        if weak and child.confidence > weak[1]:
            self.queue.remove(weak[0])
            weak_node = self.tree.get(weak[0])
            assert weak_node is not None  # queue only ever holds live tree node ids
            # lower exploitation dequeued -> N
            self._pool(weak_node)
            self.queue.add_exploitation(child.node_id, child.confidence)
            child.state = NodeState.EXPLOITATION
        else:
            self._pool(child)

    # ── queue update: refill exploitation then exploration from top of N ─
    def _refill_queue(self) -> None:
        while self.queue.has_exploitation_slot():
            nid = self.pool.pop_best_open()
            if not nid:
                break
            node = self.tree.get(nid)
            assert node is not None  # pool only ever holds ids of live tree nodes
            self.queue.add_exploitation(nid, node.confidence)
            node.state = NodeState.EXPLOITATION
        while self.queue.has_exploration_slot():
            nid = self.pool.pop_best_open()
            if not nid:
                break
            node = self.tree.get(nid)
            assert node is not None  # pool only ever holds ids of live tree nodes
            self.queue.add_exploration(nid, node.confidence)
            node.state = NodeState.EXPLORATION

    # ── helpers ─────────────────────────────────────────────────────────
    async def _generate(self, node: Node) -> list[Node]:
        thoughts = await self.llm.generate(node, self.branching, agents=self.agents)
        children = node_extraction(node, thoughts, self.tree)
        for c in children:
            # depth cap = safety net; LLM decides terminal
            if c.is_terminal:
                c.terminal_reason = "llm"
            elif c.depth >= self.max_plan_depth:
                c.is_terminal = True
                c.terminal_reason = "depth_cap"
        return children

    def _route_terminals(self, parent: Node | None, children: list[Node]) -> list[Node]:
        non_terminal = []
        for c in children:
            if not c.is_terminal:
                non_terminal.append(c)
                continue
            if (c.target or {}).get("tasks"):           # real final step IS the terminal
                c.state = NodeState.TERMINAL
                self.pool.store_terminal(c.node_id, c.confidence)   # store current terminal nodes -> N
            else:                                       # content-less "plan complete" marker:
                c.state = NodeState.PRUNED              # the parent (last real step) is the terminal
                if parent and parent.parent_id:         # never terminate at the root
                    parent.is_terminal = True
                    parent.terminal_reason = parent.terminal_reason or "llm"
                    self.pool.store_terminal(parent.node_id, parent.confidence)
        return non_terminal

    def _route_to_pool(self, parent: Node, children: list[Node]) -> None:
        for c in self._route_terminals(parent, children):
            self._pool(c)

    def _pool(self, node: Node) -> None:
        node.state = NodeState.POOLED
        self.pool.store(node.node_id, node.confidence)

    def _formulate(self, terminal_id: str) -> Plan:
        self.produced.add(terminal_id)
        terminal_node = self.tree.get(terminal_id)
        assert terminal_node is not None  # terminal_id came from pool.pop_terminal, always a stored node
        terminal_node.state = NodeState.TERMINAL
        path = self.tree.path_to_root(terminal_id)
        plan = plan_formulate(f"plan_{uuid.uuid4().hex[:12]}", self.user_input, path)
        logger.info("plan formulate | plan=%s terminal=%s targets=%d", plan.plan_id, terminal_id, len(plan.targets))
        return plan

    def _emit(self) -> None:
        if self.on_layer is not None:
            self.on_layer(self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        def _confidence(nid: str) -> float:
            node = self.tree.get(nid)
            assert node is not None  # pool only ever holds ids of live tree nodes
            return node.confidence

        members = set(self.pool.open_ids()) | set(self.pool.terminal_ids())
        pool_sorted = sorted(members, key=_confidence, reverse=True)
        nodes = []
        for nid, node in self.tree._nodes.items():
            nodes.append({
                "id": nid, "parent": node.parent_id, "depth": node.depth,
                "conf": round(node.confidence, 3), "state": node.state.value,
                "is_terminal": node.is_terminal, "terminal_reason": node.terminal_reason,
                "desc": (node.target or {}).get("description") or node.thought,
                "tasks": (node.target or {}).get("tasks") or [], "blocked": list(node.blocked_attempts),
            })
        return {
            "layer": self.layers_run, "root": self.tree.root_id, "nodes": nodes,
            "pool": pool_sorted,
            "queue": {"exploitation": list(self.queue.exploitation), "exploration": list(self.queue.exploration)},
            "terminals": [t for t in self.pool.terminal_ids() if t not in self.produced],
            "plan_ready": bool(self.pool.pop_terminal(exclude=self.produced)),
        }

    def dump_state(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "user_input": self.user_input,
            "agents": self.agents,
            "produced": list(self.produced),
            "layers_run": self.layers_run,
            "root_id": self.tree.root_id,
            "nodes": [_node_to_dict(n) for n in self.tree._nodes.values()],
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self.user_input = state.get("user_input", "")
        self.agents = state.get("agents", [])
        self.produced = set(state.get("produced", []))
        self.layers_run = state.get("layers_run", 0)
        for nd in state.get("nodes", []):
            node = _node_from_dict(nd)
            self.tree._nodes[node.node_id] = node
        self.tree.root_id = state.get("root_id")
        for node in self.tree._nodes.values():
            if node.state in (NodeState.POOLED, NodeState.EXPLOITATION, NodeState.EXPLORATION):
                node.state = NodeState.POOLED
                self.pool.store(node.node_id, node.confidence)
            elif node.state == NodeState.TERMINAL:
                self.pool.store_terminal(node.node_id, node.confidence)
