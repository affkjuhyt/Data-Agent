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
        "giao chuyên gia số (catalog/metric/SQL/pandas) làm một nhánh số liệu với ngữ cảnh hẹp; "
        "dùng khi câu hỏi cần số, hoặc song song với investigate_documents khi vừa số vừa văn bản"
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
        "giao chuyên gia tài liệu tìm đoạn văn/công văn giải thích; "
        "dùng khi cần dẫn nguồn văn bản, hoặc song song với investigate_numbers"
    )
    Args = InvestigateArgs
    timeout_seconds = 600.0

    async def run(self, ctx: ToolContext, args: InvestigateArgs) -> ToolResult:  # type: ignore[override]
        return await sp.run(sp.DOCUMENTS, ctx, args.goal, _leaf_tools())
