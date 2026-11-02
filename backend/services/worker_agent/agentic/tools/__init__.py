"""The agent's tools by name. A new capability is one Tool subclass added to TOOLS."""

from __future__ import annotations

import os

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


def python_enabled() -> bool:
    """`analyze` runs model-written Python. The child process is checked statically and by an
    audit hook, but both live inside Python and four security reviews each found a way round
    them; it stays off until the child runs behind an OS boundary (a sandbox service with no
    network, a read-only filesystem and no secrets, or nsjail/gVisor). AGENT_PYTHON_SANDBOX=local
    turns it on for a trusted deployment."""
    return os.environ.get("AGENT_PYTHON_SANDBOX", "off").strip().lower() == "local"


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        SearchCatalog(),
        DescribeMetric(),
        SearchValues(),
        QueryMetric(),
        ExploreSql(),
        *([Analyze()] if python_enabled() else []),
        SearchDocuments(),
        AskUser(),
    )
}

__all__ = ["TOOLS", "Clarify", "Tool", "ToolContext", "ToolResult"]
