"""What a tool is: a name, what it is for, the arguments it takes, and `run`.

A tool reads or computes; it never writes prose for the answer. What it found goes to the
workspace as datasets (or citations) and back to the model as an observation, a short text the
model reasons over. Adding a capability to the agent is adding one Tool to the registry.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.llm import Gateway
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.text import FoundPeriod

# What an observation may be at most when it goes back to the model.
MAX_OBSERVATION = 3000


def cut(value: Any, limit: int = MAX_OBSERVATION) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class ToolContext:
    org_id: uuid.UUID
    space_id: uuid.UUID
    user_id: uuid.UUID
    question_id: uuid.UUID
    question: str
    today: datetime.date
    as_of: datetime.date | None
    purpose: str | None
    layer: SpaceLayer
    model: Gateway
    ws: Workspace
    # The period the question names, read by rule (find_period): a year or a month in the
    # question is a period, never a member to look up.
    period: FoundPeriod | None = None
    # Model calls made inside tools (sqlgen), for the receipt.
    models: list[dict[str, Any]] = field(default_factory=list)
    step_id: str = ""


@dataclass
class Clarify:
    question: str
    options: list[str] = field(default_factory=list)


# Why a tool failed, which decides what the engine does next (AGC-02):
#   arguments    the arguments were wrong (a period not ISO, a cut the metric has not, code that
#                fails): the executor writes them again with the error, in place
#   transient    the source may come back (a timeout, a dropped connection): the same arguments
#                run once more after a pause
#   unavailable  the capability is off, or its source cannot be queried in place: no retry; the
#                planner may choose another tool
#   forbidden    the asker may not see what the step reads: no retry and no replan, so the
#                planner never looks for another way round a permission (invariant 13)
#   logic        anything else (nothing found, a step in the wrong order): the planner decides
Failure = Literal["arguments", "transient", "unavailable", "forbidden", "logic"]


@dataclass
class ToolResult:
    ok: bool
    observation: str
    datasets: list[str] = field(default_factory=list)
    # What the timeline shows of the step (never SQL text nor rows).
    detail: dict[str, Any] = field(default_factory=dict)
    clarify: Clarify | None = None
    # Read only when `ok` is False.
    failure: Failure = "logic"


class Tool:
    name: ClassVar[str]
    title: ClassVar[str]  # the bold verb on the timeline
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    # How long one run may take before the engine stops it as a transient failure; above the
    # source's own query timeout (120 s by default), so this only catches a tool that hangs.
    timeout_seconds: ClassVar[float] = 180.0

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        raise NotImplementedError

    @classmethod
    def schema(cls) -> dict[str, Any]:
        return cls.Args.model_json_schema()

    @classmethod
    def card(cls) -> str:
        """The tool as the planner and executor read it."""
        props = cls.schema().get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('description') or v.get('type') or 'any'}" for k, v in props.items()
        )
        return f"- {cls.name}({args}): {cls.description}"


ToolRunner = Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]
