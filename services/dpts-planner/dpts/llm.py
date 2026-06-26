from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field

from .node import Node
from .tree import Tree

logger = logging.getLogger(__name__)


@dataclass
class ChildThought:
    thought: str
    confidence: float
    is_terminal: bool = False
    target: dict = field(default_factory=dict)


# JSON grammar enforced at decode time (vLLM structured output). tasks minItems:1
# makes a content-less "plan complete" step impossible to emit.
STEP_SCHEMA = {
    "type": "object",
    "required": ["thought", "confidence", "is_terminal", "target"],
    "additionalProperties": False,
    "properties": {
        "thought": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "is_terminal": {"type": "boolean"},
        "target": {
            "type": "object",
            "required": ["description", "tasks"],
            "additionalProperties": False,
            "properties": {
                "description": {"type": "string"},
                "tasks": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["title", "type", "depend_on"],
                        "additionalProperties": False,
                        "properties": {
                            "title": {"type": "string"},
                            "type": {"type": "string"},
                            "depend_on": {"type": "array", "items": {"type": ["integer", "string"]}},
                        },
                    },
                },
            },
        },
    },
}

_SYSTEM = (
    "You build a plan as a SHORT ordered sequence of high-level steps (Targets), each "
    "with concrete Tasks, using tree search. On each call you propose the SINGLE best "
    "NEXT step given the GOAL and the PLAN SO FAR.\n"
    "Set is_terminal correctly — this is the most important field:\n"
    "- is_terminal=true  -> after THIS step's tasks are done, the GOAL is fully achieved; this is the LAST step.\n"
    "- is_terminal=false -> genuinely new work is still needed after this one.\n"
    "Before answering, check: does the GOAL truly need ANOTHER step after this? If the GOAL's "
    "deliverable (a report, a value, a file, an answer) is produced by this step, is_terminal MUST be true.\n"
    "NEVER propose a step that repeats or rephrases work already in PLAN SO FAR — if the remaining work "
    "is already covered, set is_terminal=true instead of adding a step.\n"
    "Keep plans tight (usually 2-3 steps); never pad. Every step has at least one concrete task. "
    "Score confidence in [0,1].\n"
    "Reply with ONLY one JSON object: {thought, confidence, is_terminal, "
    "target:{description, tasks:[{title, type, depend_on}]}}."
)
_INSTRUCTION = (
    "Propose the next step now. Decide is_terminal: true if this step completes the GOAL, "
    "otherwise false."
)


# prefix-stable prompt (system+goal+path shared -> vLLM prefix cache); avoid suffix breaks cache only on recovery
def build_messages(goal: str, path_steps: list[str], avoid: list[str] | None) -> list[dict]:
    steps = "".join(f"\n- {s}" for s in path_steps)
    user = f"GOAL:\n{goal}\n\nPLAN SO FAR ({len(path_steps)} steps):{steps or ' (none yet)'}\n\n"
    if avoid:
        user += "These next-steps FAILED in execution; do NOT propose them again:" + \
                "".join(f"\n- {a}" for a in avoid) + "\n\n"
    user += _INSTRUCTION
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


class LLM:
    """Thought generator that goes through the shared model-service connector
    (infra/model-service /generate -> vLLM). STEP_SCHEMA is passed as the strict
    JSON grammar. The model-service returns one completion per call, so K children
    = K concurrent /generate calls (vLLM continuous-batches them); temperature>0
    gives the branch diversity."""

    def __init__(self, branching: int = 2, temperature: float = 0.7, max_tokens: int = 360) -> None:
        self.branching = branching
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def generate(self, node: Node, k: int | None = None) -> list[ChildThought]:
        import asyncio
        from services.database_connector.model_connector import client as model_client
        k = k or self.branching
        messages = build_messages(node.goal or node.thought, node.path_steps, node.blocked_attempts or None)

        async def one_child() -> list[ChildThought]:
            content = await model_client.generate(
                messages=messages, json_schema=STEP_SCHEMA, json_mode=True,
                max_tokens=self.max_tokens, temperature=self.temperature,
            )
            return _parse(content)

        results = await asyncio.gather(*(one_child() for _ in range(k)))
        out = [ct for r in results for ct in r]
        out.sort(key=lambda c: c.confidence, reverse=True)
        return out[:k]


class MockLLM:
    """Deterministic LLM stand-in (tests / offline)."""

    def __init__(self, plan_depth: int = 3, branching: int = 3, seed: int = 7) -> None:
        self.plan_depth = plan_depth
        self.branching = branching
        self.seed = seed

    async def generate(self, node: Node, k: int | None = None) -> list[ChildThought]:
        k = k or self.branching
        cd = node.depth + 1
        terminal = cd >= self.plan_depth
        off = len(node.blocked_attempts)  # recovery shifts options -> different children
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


# node extraction -> build Node objects from LLM thoughts -> store nodes -> Tree
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


def _loads_lenient(content: str) -> list:
    content = content.strip()
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj.get("steps", [obj]) if "steps" in obj else [obj]
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass
    salvaged, depth, start = [], 0, None  # salvage objects from truncated JSON
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
