"""Lead-facing tools that spawn a numbers or documents specialist (own transcript, shared workspace)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.worker_agent.agentic import specialists as sp
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult

SPECIALIST_TOOL_NAMES = frozenset({"investigate_numbers", "investigate_documents"})


def _leaf_tools() -> dict[str, Tool]:
    # Lazy: TOOLS is built in __init__ after this module loads; specialists must not re-enter.
    from services.worker_agent.agentic.tools import TOOLS

    return {n: t for n, t in TOOLS.items() if n not in SPECIALIST_TOOL_NAMES}


class InvestigateArgs(BaseModel):
    goal: str = Field(description="việc chuyên gia cần làm, một câu rõ ràng")


class InvestigateNumbers(Tool):
    name = "investigate_numbers"
    title = "Điều tra số liệu"
    description = (
        "giao chuyên gia numbers (xem thẻ whenToUse trong # Chuyên gia): catalog/metric/SQL/"
        "pandas trong ngữ cảnh hẹp; chỉ nhận summary — không thấy transcript trong"
    )
    Args = InvestigateArgs
    # Several inner tool runs; each leaf tool already has its own timeout.
    timeout_seconds = 600.0

    async def run(self, ctx: ToolContext, args: InvestigateArgs) -> ToolResult:  # type: ignore[override]
        return await sp.run(sp.NUMBERS, ctx, args.goal, _leaf_tools())


class InvestigateDocuments(Tool):
    name = "investigate_documents"
    title = "Điều tra tài liệu"
    description = (
        "giao chuyên gia documents (xem thẻ whenToUse trong # Chuyên gia): tìm đoạn văn/"
        "công văn; chỉ nhận summary — không thấy transcript trong"
    )
    Args = InvestigateArgs
    timeout_seconds = 600.0

    async def run(self, ctx: ToolContext, args: InvestigateArgs) -> ToolResult:  # type: ignore[override]
        return await sp.run(sp.DOCUMENTS, ctx, args.goal, _leaf_tools())
