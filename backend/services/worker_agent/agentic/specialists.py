"""Specialists: Claude Code-style subagents for the dynamic Ask engine.

The lead planner sees only a specialist's summary. Each specialist has its own short transcript,
a fixed tool allowlist, and writes datasets/citations onto the shared Workspace (blackboard).
Peer messaging and nested specialists are out of scope: parent → child → summary only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult, cut
from services.worker_agent.llm import ModelUnavailable

logger = logging.getLogger(__name__)

# Inner steps per specialist call: enough for catalog → query (or one search), not a second plan.
DEFAULT_INNER_STEPS = 4


class StepChoice(BaseModel):
    """One turn of a specialist: either run a tool, or finish with a summary for the lead."""

    done: bool = False
    tool: str | None = None
    instruction: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


@dataclass(frozen=True)
class Specialist:
    name: str
    title: str
    tool_names: frozenset[str]
    system: str
    # OP-2: planner-facing card (Claude Code agent frontmatter shape, product wording).
    when_to_use: str = ""
    max_inner_steps: int = DEFAULT_INNER_STEPS

    def card(self) -> str:
        """What the lead planner sees: whenToUse, allowlist, hard denials — never the transcript."""
        tools = ", ".join(sorted(self.tool_names)) or "(không)"
        when = self.when_to_use or self.title
        return (
            f"- {self.name} ({self.title}): whenToUse={when}; tools=[{tools}]; "
            "disallowed=mọi công cụ ngoài danh sách, không soạn câu trả lời cuối cho người hỏi"
        )


NUMBERS = Specialist(
    name="numbers",
    title="Chuyên gia số",
    tool_names=frozenset(
        {
            "search_catalog",
            "describe_metric",
            "search_values",
            "data_coverage",
            "query_metric",
            "explain_change",
            "explore_sql",
            "analyze",
            "forecast_metric",
        }
    ),
    when_to_use=(
        "câu hỏi cần số / so sánh / xu hướng / xếp hạng / dự báo trên chỉ số hoặc SQL khám phá; "
        "hoặc nhánh số của câu hybrid (song song với documents)"
    ),
    system=(
        "Bạn là chuyên gia số liệu trong Data Space. Chỉ dùng các công cụ số được liệt kê. "
        "Không tìm tài liệu, không viết câu trả lời cuối. Khi đủ số trên workspace, done=true "
        "và summary mô tả dataset/con số đã có (không bịa số ngoài quan sát). "
        "Câu hỏi dự báo / forecast: dùng forecast_metric (không bịa số dự báo bằng lời)."
    ),
)

DOCUMENTS = Specialist(
    name="documents",
    title="Chuyên gia tài liệu",
    tool_names=frozenset({"search_documents"}),
    when_to_use=(
        "câu hỏi cần dẫn nguồn công văn / ghi chú / chính sách; "
        "hoặc nhánh văn bản của câu hybrid (song song với numbers)"
    ),
    system=(
        "Bạn là chuyên gia tài liệu trong Data Space. Chỉ dùng search_documents. "
        "Không truy vấn số liệu. Khi đủ đoạn trích để dẫn nguồn, done=true và summary liệt kê "
        "các cite [n] và ý chính (không bịa nội dung ngoài đoạn đã tìm)."
    ),
)


def planner_cards() -> str:
    """Cards for the lead planner prompt (investigate_* tools stay the call surface)."""
    return "\n".join(s.card() for s in (NUMBERS, DOCUMENTS))


def _cards(tools: dict[str, Tool], allow: frozenset[str]) -> str:
    return "\n".join(tools[n].card() for n in sorted(allow) if n in tools)


async def run(
    specialist: Specialist,
    ctx: ToolContext,
    goal: str,
    tools: dict[str, Tool],
) -> ToolResult:
    """Run the specialist until it finishes or hits its inner step budget.

    Datasets and citations land on `ctx.ws`. Only `observation` (the summary) goes back to the
    lead's plan step — the inner transcript stays here.
    """
    allow = frozenset(n for n in specialist.tool_names if n in tools)
    if not allow:
        return ToolResult(
            False,
            f"chuyên gia {specialist.name}: không có công cụ nào khả dụng",
            failure="unavailable",
        )
    transcript: list[str] = []
    datasets: list[str] = []
    # Surface ids Ask needs on the lead step (e.g. forecast_id → "Mở studio"), not the full
    # inner transcript.
    forwarded: dict[str, Any] = {}
    system = (
        f"{specialist.system}\n\n# Công cụ được phép\n{_cards(tools, allow)}\n\n"
        "# Định dạng\n"
        'Trả về JSON: {"done": false, "tool": "<tên>", "instruction": "...", "args": {...}, '
        '"summary": ""} khi còn việc; hoặc {"done": true, "tool": null, "instruction": "", '
        '"args": {}, "summary": "<tóm tắt cho trưởng nhóm>"} khi xong.'
    )
    for _ in range(specialist.max_inner_steps):
        user = (
            f"<goal>\n{goal}\n</goal>\n"
            f"<question>\n{ctx.question}\n</question>\n"
            f"<datasets>\n{ctx.ws.summary()}\n</datasets>\n"
            f"<transcript>\n" + ("\n".join(transcript) if transcript else "(trống)") + "\n"
            "</transcript>"
        )
        try:
            reply = await ctx.model.json_chat("planner", system, user, StepChoice, max_tokens=900)
        except ModelUnavailable as exc:
            return ToolResult(
                False,
                f"chuyên gia {specialist.name}: model không trả lời ({exc})",
                failure="unavailable",
            )
        choice = reply.content
        if choice.done:
            summary = (choice.summary or "đã xong").strip()
            return ToolResult(
                True,
                cut(summary),
                datasets=list(dict.fromkeys(datasets)),
                detail={
                    "specialist": specialist.name,
                    "inner_steps": len(transcript),
                    **forwarded,
                },
            )
        name = (choice.tool or "").strip()
        if name not in allow:
            return ToolResult(
                False,
                f"chuyên gia {specialist.name}: không được dùng công cụ {name or '(trống)'}",
                failure="logic",
                detail={"specialist": specialist.name, "refused_tool": name},
            )
        tool = tools[name]
        try:
            args = tool.Args.model_validate(choice.args)
        except ValidationError as exc:
            transcript.append(f"- {name}: tham số sai ({exc.error_count()} lỗi)")
            continue
        try:
            result = await tool.run(ctx, args)
        except Exception as exc:  # tool failure is feedback inside the specialist
            logger.exception("specialist %s tool %s failed", specialist.name, name)
            transcript.append(f"- {name}: lỗi {type(exc).__name__}")
            continue
        datasets.extend(result.datasets)
        if isinstance(result.detail.get("forecast_id"), str):
            forwarded["forecast_id"] = result.detail["forecast_id"]
        note = choice.instruction.strip() or name
        transcript.append(f"- {name} ({note}): {cut(result.observation, 500)}")
        if result.clarify is not None:
            return ToolResult(
                result.ok,
                cut(result.observation),
                datasets=list(dict.fromkeys(datasets)),
                detail={"specialist": specialist.name, **forwarded},
                clarify=result.clarify,
                failure=result.failure,
            )
        if not result.ok and result.failure == "forbidden":
            return ToolResult(
                False,
                cut(result.observation),
                datasets=list(dict.fromkeys(datasets)),
                detail={"specialist": specialist.name, **forwarded},
                failure="forbidden",
            )
    return ToolResult(
        False,
        f"chuyên gia {specialist.name}: hết {specialist.max_inner_steps} bước trong mà chưa tóm tắt",
        datasets=list(dict.fromkeys(datasets)),
        failure="logic",
        detail={"specialist": specialist.name, "transcript": transcript[-6:], **forwarded},
    )
