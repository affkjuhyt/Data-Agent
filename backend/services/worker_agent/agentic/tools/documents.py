"""The Data Space's documents (policies, definitions, reports): passages the answer may cite.
Retrieval is the same as the documents path (documents/retrieve.py); the passages become the
turn's citations, numbered so the reporter can cite them as [1], [2]."""

from __future__ import annotations

from pydantic import BaseModel, Field

from libs.dataagent_db import scoped_session
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult, cut
from services.worker_agent.documents.retrieve import retrieve

ROLE = "svc_qa"
PASSAGE_CHARS = 600


class SearchDocumentsArgs(BaseModel):
    query: str = Field(description="điều cần tìm trong tài liệu")


class SearchDocuments(Tool):
    name = "search_documents"
    title = "Tìm trong tài liệu"
    description = (
        "tìm đoạn văn trong tài liệu của Data Space (quy định, định nghĩa, báo cáo) để giải thích "
        "hoặc dẫn nguồn; trả về đoạn trích đánh số để trích dẫn [n]"
    )
    Args = SearchDocumentsArgs

    async def run(self, ctx: ToolContext, args: SearchDocumentsArgs) -> ToolResult:  # type: ignore[override]
        from services.worker_agent import questions

        async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
            passages = await retrieve(
                session,
                questions.search_services(),
                data_space_id=ctx.space_id,
                question=args.query,
                as_of=ctx.as_of or ctx.today,
                purpose=ctx.purpose,
            )
        if not passages:
            return ToolResult(False, "không thấy đoạn nào trong tài liệu", detail={"found": 0})
        found = []
        for p in passages[:6]:
            index = len(ctx.ws.citations) + 1
            ctx.ws.citations.append(
                {
                    "index": index,
                    "source": p.title,
                    "type": "document",
                    "snippet": p.text[:PASSAGE_CHARS],
                    "doc_id": str(p.document_id),
                    "page": p.page_from,
                    "chunk_id": str(p.chunk_id),
                }
            )
            found.append(
                {
                    "cite": f"[{index}]",
                    "title": p.title,
                    "page": p.page_from,
                    "text": p.text[:PASSAGE_CHARS],
                }
            )
        return ToolResult(
            True,
            cut(found, 4000),
            detail={"hits": [{"title": f["title"], "page": f["page"]} for f in found]},
        )
