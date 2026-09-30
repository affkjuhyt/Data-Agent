from __future__ import annotations

from typing import Any, cast
import json
import logging
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import litellm

from .node import Node
from .tree import Tree

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / ".." / ".." / "prompts"


@lru_cache(maxsize=1)
def _system_base() -> str:
    return (_PROMPTS_DIR / "planner_system.txt").read_text(encoding="utf-8").strip()


@dataclass
class ChildThought:
    thought: str
    confidence: float
    is_terminal: bool = False
    target: dict[str, Any] = field(default_factory=dict)


_INSTRUCTION = (
    "Propose the next step now. Decide is_terminal: true if this step completes the GOAL, "
    "otherwise false."
)


def _build_agents_block(agents: list[dict[str, Any]]) -> str:
    if not agents:
        return ""
    lines = ["\n\nAVAILABLE AGENTS (use agent_id exactly as listed):"]
    for a in agents:
        desc = (a.get("description") or "").strip()
        lines.append(f'- agent_id: "{a["id"]}"  name: {a["name"]}' + (f"  →  {desc}" if desc else ""))
    return "\n".join(lines)


def build_messages(
    goal: str,
    path_steps: list[str],
    avoid: list[str] | None,
    agents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    system = _system_base() + _build_agents_block(agents or [])
    steps = "".join(f"\n- {s}" for s in path_steps)
    user = f"GOAL:\n{goal}\n\nPLAN SO FAR ({len(path_steps)} steps):{steps or ' (none yet)'}\n\n"
    if avoid:
        user += "These next-steps FAILED in execution; do NOT propose them again:" + \
                "".join(f"\n- {a}" for a in avoid) + "\n\n"
    user += _INSTRUCTION
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class LLM:
    """Calls LiteLLM gateway (OpenAI-compatible) to generate plan steps."""

    def __init__(self, branching: int = 2, temperature: float = 0.7, max_tokens: int = 512) -> None:
        self.branching = branching
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def generate(
        self, node: Node, k: int | None = None, agents: list[dict[str, Any]] | None = None
    ) -> list[ChildThought]:
        import asyncio
        from services.workflow_runtime.application.orchestrator import config
        k = k or self.branching
        messages = build_messages(
            node.goal or node.thought,
            node.path_steps,
            node.blocked_attempts or None,
            agents=agents,
        )

        from services.workflow_runtime.application.runtime_services import llm_call

        # Each of the k candidates running in parallel is its own generation.
        async def one_child() -> list[ChildThought]:
            resp = await llm_call.acompletion(
                "llm:tot_planner", litellm.acompletion,
                model=config.PLANNER_MODEL,
                api_base=config.LITELLM_BASE_URL,
                api_key=config.LITELLM_API_KEY,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            return _parse(content)

        results = await asyncio.gather(*(one_child() for _ in range(k)), return_exceptions=True)
        out: list[ChildThought] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("llm generate error: %s", r)
                continue
            # asyncio.gather(..., return_exceptions=True) types each result as
            # list[ChildThought] | BaseException; the Exception check above
            # doesn't statically rule out a non-Exception BaseException (e.g.
            # CancelledError), so narrow explicitly instead of changing the check.
            out.extend(cast(list[ChildThought], r))
        out.sort(key=lambda c: c.confidence, reverse=True)
        return out[:k]


class MockLLM:
    """Deterministic stand-in for dev/test."""

    def __init__(self, plan_depth: int = 3, branching: int = 3, seed: int = 7) -> None:
        self.plan_depth = plan_depth
        self.branching = branching
        self.seed = seed

    async def generate(self, node: Node, k: int | None = None, agents: list[dict[str, Any]] | None = None) -> list[ChildThought]:
        k = k or self.branching
        cd = node.depth + 1
        terminal = cd >= self.plan_depth
        off = len(node.blocked_attempts)
        out = []
        for j in range(k):
            i = j + off
            conf = round(random.Random(f"{self.seed}:{node.node_id}:{i}").uniform(0.3, 0.99), 4)
            out.append(ChildThought(
                thought=f"step d{cd} #{i}", confidence=conf, is_terminal=terminal,
                target={"description": f"target d{cd} opt {i}",
                        "tasks": [{"title": f"t{cd}.{i}a", "type": "data", "depend_on": []},
                                  {"title": f"t{cd}.{i}b", "type": "transform", "depend_on": [0]}]}))
        return out


def node_extraction(parent: Node, thoughts: list[ChildThought], tree: Tree) -> list[Node]:
    nodes: list[Node] = []
    for ct in thoughts:
        step_desc = (ct.target or {}).get("description") or ct.thought
        node = Node(
            thought=ct.thought, confidence=ct.confidence, parent_id=parent.node_id,
            depth=parent.depth + 1, is_terminal=ct.is_terminal,
            target=ct.target or {}, goal=parent.goal,
            path_steps=parent.path_steps + [step_desc],
        )
        tree.store(node)
        nodes.append(node)
    return nodes


def _parse(content: str) -> list[ChildThought]:
    out = []
    for s in _loads_lenient(content):
        if not isinstance(s, dict):
            continue
        try:
            conf = min(max(float(s.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            conf = 0.5
        out.append(ChildThought(
            thought=str(s.get("thought", ""))[:500], confidence=conf,
            is_terminal=bool(s.get("is_terminal", False)), target=s.get("target") or {}))
    return out


def _loads_lenient(content: str) -> list[Any]:
    content = content.strip()
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj.get("steps", [obj]) if "steps" in obj else [obj]
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass
    salvaged, depth, start = [], 0, None
    for i, ch in enumerate(content):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    o = json.loads(content[start:i + 1])
                    if isinstance(o, dict) and ("thought" in o or "target" in o):
                        salvaged.append(o)
                except json.JSONDecodeError:
                    pass
                start = None
    return salvaged
