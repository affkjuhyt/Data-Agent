"""The agent's tools by name. A new capability is one Tool subclass added to TOOLS."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.worker_agent.agentic.tools.analyze import Analyze
from services.worker_agent.agentic.tools.base import Clarify, Tool, ToolContext, ToolResult
from services.worker_agent.agentic.tools.catalog import DescribeMetric, SearchCatalog, SearchValues
from services.worker_agent.agentic.tools.documents import SearchDocuments
from services.worker_agent.agentic.tools.query import ExploreSql, QueryMetric


class AskUserArgs(BaseModel):
    question: str = Field(description="câu hỏi lại người dùng")
    options: list[str] = Field(default_factory=list, description="các lựa chọn gợi ý, có thể rỗng")


class AskUser(Tool):
    name = "ask_user"
    title = "Hỏi lại bạn"
    description = (
        "khi thiếu thông tin không thể tự tìm (câu hỏi mơ hồ, nhiều cách hiểu khác số liệu)"
    )
    Args = AskUserArgs

    async def run(self, ctx: ToolContext, args: AskUserArgs) -> ToolResult:  # type: ignore[override]
        return ToolResult(
            True,
            "đã hỏi lại",
            detail={"question": args.question, "options": args.options},
            clarify=Clarify(args.question, args.options[:5]),
        )


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        SearchCatalog(),
        DescribeMetric(),
        SearchValues(),
        QueryMetric(),
        ExploreSql(),
        Analyze(),
        SearchDocuments(),
        AskUser(),
    )
}

__all__ = ["TOOLS", "Clarify", "Tool", "ToolContext", "ToolResult"]
