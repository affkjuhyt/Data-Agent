"""The Data Space's documents (policies, definitions, reports): passages the answer may cite.
Retrieval is the same as the documents path (documents/retrieve.py); the passages become the
turn's citations, numbered so the reporter can cite them as [1], [2].

A number asked of a table the search found is read from its cell, as the governed path does
(documents/table_query, DI-12, 24/11 for the agent): the figure becomes a dataset of the turn,
trusted as a document, cited to its cell (table, row, column, page, box), so the answer's number
is the cell's and passes the number check. Two cells as good with other numbers are given back
both, for the agent to ask which, never one guessed."""

from __future__ import annotations

from typing import Any

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
            read = await questions._from_tables(session, args.query, passages) if passages else None
        if not passages:
            return ToolResult(False, "không thấy đoạn nào trong tài liệu", detail={"found": 0})
        found: list[dict[str, Any]] = []
        cells = _cells(ctx, read, passages)
        # One handle per passage for the whole turn (ragflow's evidence registry): a passage two
        # searches find is cited by the number it got the first time.
        known = {c.get("chunk_id"): c["index"] for c in ctx.ws.citations}
        for p in passages[:6]:
            if str(p.chunk_id) in known:
                index = known[str(p.chunk_id)]
                found.append(
                    {
                        "cite": f"[{index}]",
                        "title": p.title,
                        "page": p.page_from,
                        "text": p.text[:PASSAGE_CHARS],
                    }
                )
                continue
            index = len(ctx.ws.citations) + 1
            known[str(p.chunk_id)] = index
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
            cut([*cells, *found], 4000),
            datasets=[str(c["dataset"]) for c in cells if "dataset" in c],
            detail={
                "hits": [{"title": f["title"], "page": f["page"]} for f in found],
                **({"cells": len(cells)} if cells else {}),
            },
        )


def _cells(ctx: ToolContext, read: Any, passages: list[Any]) -> list[dict[str, Any]]:
    """The table cell the query asks for, as a cited dataset; or both candidates when two fit."""
    from services.worker_agent.documents import table_query

    if read is None:
        return []
    if isinstance(read, table_query.Ambiguous):
        return [
            {
                "ambiguous": "hai ô khớp như nhau, cần hỏi lại người dùng muốn ô nào",
                "options": [
                    {"row": o.row_label, "column": o.column_label, "value": str(o.value)}
                    for o in read.options
                ],
            }
        ]
    found = read
    cell = found.cell
    passage = next(
        (p for p in passages if getattr(p, "table_id", None) == found.table_id), passages[0]
    )
    index = len(ctx.ws.citations) + 1
    ctx.ws.citations.append(
        {
            "index": index,
            "source": passage.title,
            "type": "document",
            "kind": "table_cell",
            "snippet": f"{found.row_label} - {found.column_label}: {cell.text}",
            "doc_id": str(passage.document_id),
            "page": cell.page,
            "chunk_id": str(passage.chunk_id),
            "cell": {
                "table_id": str(found.table_id),
                "row": cell.row,
                "col": cell.col,
                "page": cell.page,
                "bbox": cell.bbox,
                "text": cell.text,
                "value": str(found.value),
                "unit": found.unit,
            },
        }
    )
    ds = ctx.ws.add(
        title=found.caption or f"{found.row_label} - {found.column_label}",
        columns=["hàng", "cột", "giá trị"],
        rows=[[found.row_label, found.column_label, found.value]],
        trust="document",
        step_id=ctx.step_id,
        tool="search_documents",
        unit=found.unit,
    )
    return [
        {
            "cell": f"[{index}]",
            "dataset": ds.id,
            "row": found.row_label,
            "column": found.column_label,
            "value": table_query.spoken(found.value, found.unit),
        }
    ]
