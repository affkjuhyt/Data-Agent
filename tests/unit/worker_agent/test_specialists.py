"""Specialists: a narrow tool allowlist, own transcript, summary back to the lead (Claude Code subagent shape)."""

from __future__ import annotations

import asyncio
import datetime
import types
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, Field

from services.worker_agent.agentic import specialists as sp
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.llm import Reply

pytestmark = pytest.mark.unit


class _Args(BaseModel):
    q: str = Field(default="")


class _OkTool(Tool):
    name = "probe_ok"
    title = "Probe"
    description = "writes a dataset"
    Args = _Args

    async def run(self, ctx: ToolContext, args: _Args) -> ToolResult:  # type: ignore[override]
        ds = ctx.ws.add(
            title="probe",
            columns=["v"],
            rows=[[42]],
            trust="certified",
            tool=self.name,
        )
        return ToolResult(True, f"đã có {ds.id}", datasets=[ds.id], detail={"q": args.q})


class _ForbiddenTool(Tool):
    name = "probe_forbidden"
    title = "Forbidden"
    description = "must not run inside numbers specialist"
    Args = _Args

    async def run(self, ctx: ToolContext, args: _Args) -> ToolResult:  # type: ignore[override]
        return ToolResult(True, "should not run", datasets=[])


class _Scripted:
    """json_chat returns the next scripted payload for the specialist's StepChoice."""

    def __init__(self, scripts: list[dict[str, Any]]) -> None:
        self.scripts = list(scripts)
        self.systems: list[str] = []
        self.users: list[str] = []

    async def json_chat(self, role, system, user, output, **_):  # type: ignore[no-untyped-def]
        self.systems.append(system)
        self.users.append(user)
        raw = self.scripts.pop(0)
        return Reply(output.model_validate(raw), "planner", 3, 1)


def _ctx(model: Any, tools: dict[str, Tool]) -> ToolContext:
    return ToolContext(
        org_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question="Doanh thu giảm — có công văn giải thích không?",
        today=datetime.date(2026, 11, 5),
        as_of=None,
        purpose=None,
        layer=types.SimpleNamespace(vocabulary=[]),  # type: ignore[arg-type]
        model=model,
        ws=Workspace(),
    )


def test_specialist_runs_only_its_tools_and_returns_a_summary() -> None:
    tools = {"probe_ok": _OkTool(), "probe_forbidden": _ForbiddenTool()}
    model = _Scripted(
        [
            {
                "done": False,
                "tool": "probe_ok",
                "instruction": "lấy số",
                "args": {"q": "revenue"},
                "summary": "",
            },
            {
                "done": True,
                "tool": None,
                "instruction": "",
                "args": {},
                "summary": "Doanh thu probe = 42 trên dataset d1.",
            },
        ]
    )
    spec = sp.Specialist(
        name="numbers",
        title="Chuyên gia số",
        tool_names=frozenset({"probe_ok"}),
        system="chỉ dùng probe_ok",
        max_inner_steps=3,
    )
    result = asyncio.run(sp.run(spec, _ctx(model, tools), "lấy doanh thu", tools))
    assert result.ok
    assert result.datasets == ["d1"]
    assert "42" in result.observation
    assert "probe_ok" in model.systems[0]
    assert "probe_forbidden" not in model.systems[0]


def test_specialist_rejects_a_tool_outside_its_allowlist() -> None:
    tools = {"probe_ok": _OkTool(), "probe_forbidden": _ForbiddenTool()}
    model = _Scripted(
        [
            {
                "done": False,
                "tool": "probe_forbidden",
                "instruction": "x",
                "args": {},
                "summary": "",
            },
        ]
    )
    spec = sp.Specialist(
        name="numbers",
        title="Chuyên gia số",
        tool_names=frozenset({"probe_ok"}),
        system="x",
        max_inner_steps=2,
    )
    result = asyncio.run(sp.run(spec, _ctx(model, tools), "goal", tools))
    assert not result.ok
    assert "không được dùng" in result.observation


def test_hybrid_tools_are_registered_for_the_planner() -> None:
    from services.worker_agent.agentic.tools import TOOLS

    assert "investigate_numbers" in TOOLS
    assert "investigate_documents" in TOOLS
    assert TOOLS["investigate_numbers"].name == "investigate_numbers"
    # Numbers may not open the document search; documents may not open SQL.
    assert "search_documents" not in sp.NUMBERS.tool_names
    assert "explore_sql" not in sp.DOCUMENTS.tool_names
    assert "query_metric" in sp.NUMBERS.tool_names
    assert "search_documents" in sp.DOCUMENTS.tool_names
