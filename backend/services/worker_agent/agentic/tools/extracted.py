"""count_documents: count or add up across a whole collection of documents, from rows (DI-17).

"Bao nhiêu hợp đồng có điều khoản phạt > 5%", "tổng giá trị các hoá đơn quý 1": the passages a
search returns are the top few, never the corpus, so such a question is answered from the
collection's extracted dataset (documents/extracted_dataset.py): one row per document in force,
values checked against their quote or fixed by a person.

The model picks the collection, the measure, the field and the conditions; it writes no SQL. The
statement is built here from the schema's own columns (names checked against the table, values
read as the field's type, the way the values were) and run by the executor like any query of the
workspace, so the asker's scope and access policies hold (invariant 13). Every result carries its
coverage: the documents in force, how many are counted, how many wait for review or were not read.
Its rows are trusted as a document ("Theo tài liệu"), and the documents counted are cited.
"""

from __future__ import annotations

import datetime
import decimal
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from libs.dataagent_db import scoped_session
from libs.tabular import profile as ingest_profile
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.documents import extracted_dataset as ed
from services.worker_agent.failures import is_transient
from services.worker_agent.query.executor import run_query
from services.worker_agent.understand.text import norm

ROLE = "svc_qa"
# The documents named as citations of one count, at most.
CITED = 5
# Groups a count by a field returns at most.
MAX_GROUPS = 50

Op = Literal["=", "!=", ">", ">=", "<", "<=", "contains", "empty", "not_empty"]
Measure = Literal["count", "sum", "avg", "min", "max"]
_SQL_OPS = {"=": "=", "!=": "<>", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
_MEASURE_LABEL = {
    "sum": "Tổng",
    "avg": "Trung bình",
    "min": "Nhỏ nhất",
    "max": "Lớn nhất",
}


class Condition(BaseModel):
    field: str = Field(description="tên trường của lược đồ trích")
    op: Op = Field(description="phép so sánh; contains cho chữ; empty/not_empty không cần value")
    value: str | None = Field(default=None, description="giá trị như tài liệu viết, ví dụ 5%")


class CountDocumentsArgs(BaseModel):
    collection: str | None = Field(
        default=None, description="bộ sưu tập tài liệu (tên hoặc mã); bỏ trống khi chỉ có một"
    )
    measure: Measure = Field(default="count", description="count: đếm tài liệu; sum/avg/min/max")
    field: str | None = Field(default=None, description="trường cần cộng/so (bỏ trống khi count)")
    where: list[Condition] = Field(default_factory=list, description="điều kiện, nối bằng AND")
    group_by: str | None = Field(default=None, description="trường để chia nhóm, có thể bỏ trống")


class Refused(ValueError):
    """An argument the schema does not allow: the executor writes the arguments again."""


def _q(name: str) -> str:
    # Names come from columns_of: [a-z][a-z0-9_]*; quoted anyway, never taken from the model.
    return '"' + name.replace('"', "") + '"'


def _literal(column: ed.Column, value: Any) -> str:
    if value is None:
        return "NULL"
    if column.type == "numeric":
        number = decimal.Decimal(value)
        if not number.is_finite():
            raise Refused(f"{column.field}: không phải số")
        return format(number, "f")
    if column.type == "date":
        return f"DATE '{value.isoformat()}'"
    if column.type == "boolean":
        return "TRUE" if value else "FALSE"
    return "'" + str(value).replace("'", "''") + "'"


def _column(target: ed.Target, name: str | None) -> ed.Column:
    wanted = norm(str(name or "")).replace(" ", "_")
    for c in target.columns:
        if c.field is None:
            continue
        if wanted in {norm(c.field).replace(" ", "_"), c.name, norm(c.label).replace(" ", "_")}:
            if c.pii:
                raise Refused(f"trường {c.field} là dữ liệu cá nhân, không đếm hay lọc theo nó")
            return c
    fields = ", ".join(c.field for c in target.columns if c.field and not c.pii)
    raise Refused(f"không có trường {name!r}; các trường: {fields}")


def _condition(target: ed.Target, cond: Condition, profile: ingest_profile.Profile) -> str:
    column = _column(target, cond.field)
    col = _q(column.name)
    if cond.op == "empty":
        return f"{col} IS NULL"
    if cond.op == "not_empty":
        return f"{col} IS NOT NULL"
    if cond.value is None or not cond.value.strip():
        raise Refused(f"{column.field}: thiếu giá trị để so")
    if cond.op == "contains":
        if column.type != "text":
            raise Refused(f"{column.field}: contains chỉ dùng cho chữ")
        needle = cond.value.strip().replace("'", "''")
        return f"strpos(lower({col}), lower('{needle}')) > 0"
    field = next(f for f in target.fields if str(f["name"]) == column.field)
    try:
        # The value read as the field's values were: "5%" is 5, "1.000.000 đồng" is 1000000.
        value = ed.typed(field, cond.value, profile)
    except (ValueError, decimal.InvalidOperation, ArithmeticError) as exc:
        raise Refused(f"{column.field}: {cond.value!r} không đọc được theo kiểu trường") from exc
    if cond.op not in ("=", "!=") and column.type not in ("numeric", "date"):
        raise Refused(f"{column.field}: {cond.op} chỉ dùng cho số hoặc ngày")
    return f"{col} {_SQL_OPS[cond.op]} {_literal(column, value)}"


def where_of(
    target: ed.Target, args: CountDocumentsArgs, day: datetime.date, profile: ingest_profile.Profile
) -> str:
    """The rows a count is over: in force at `day` (an expired document is not counted even
    before its row leaves the table), then the conditions."""
    when = f"DATE '{day.isoformat()}'"
    parts = [
        f"(effective_from IS NULL OR effective_from <= {when})",
        f"(effective_to IS NULL OR effective_to >= {when})",
        *(_condition(target, c, profile) for c in args.where),
    ]
    return " AND ".join(parts)


def count_sql(
    target: ed.Target, args: CountDocumentsArgs, day: datetime.date, profile: ingest_profile.Profile
) -> tuple[str, list[str]]:
    """(statement, column labels). A sum also says how many documents gave the value, so a total
    over documents that lack it is never read as a total over all of them."""
    table = f"workspace.{_q(target.table)}"
    where = where_of(target, args, day, profile)
    select: list[str] = []
    labels: list[str] = []
    group = None
    if args.group_by:
        group = _column(target, args.group_by)
        select.append(f"{_q(group.name)} AS nhom")
        labels.append(group.label)
    select.append("COUNT(*) AS so_tai_lieu")
    labels.append("Số tài liệu")
    if args.measure != "count":
        measured = _column(target, args.field)
        if measured.type != "numeric" and not (
            measured.type == "date" and args.measure in ("min", "max")
        ):
            raise Refused(f"{measured.field}: {args.measure} chỉ dùng cho trường số")
        select.append(f"{args.measure.upper()}({_q(measured.name)}) AS gia_tri")
        labels.append(f"{_MEASURE_LABEL[args.measure]} {measured.label}")
        select.append(f"COUNT({_q(measured.name)}) AS so_tai_lieu_co_gia_tri")
        labels.append(f"Số tài liệu có {measured.label}")
    sql = f"SELECT {', '.join(select)} FROM {table} WHERE {where}"
    if group is not None:
        sql += f" GROUP BY {_q(group.name)} ORDER BY so_tai_lieu DESC LIMIT {MAX_GROUPS}"
    return sql, labels


def cited_sql(
    target: ed.Target, args: CountDocumentsArgs, day: datetime.date, profile: ingest_profile.Profile
) -> str:
    where = where_of(target, args, day, profile)
    return (
        "SELECT document_id, doc_no, document_title, source_spans FROM "
        f"workspace.{_q(target.table)} WHERE {where} ORDER BY document_title, document_id "
        f"LIMIT {CITED}"
    )


def coverage_text(found: dict[str, int]) -> str:
    """The coverage block an answer from extracted rows always carries (RG-08)."""
    text = (
        f"Đếm trên dữ liệu trích từ tài liệu: {found['extracted']}/{found['documents_in_scope']} "
        "tài liệu đang hiệu lực đã được trích và tính"
    )
    left = []
    if found["needs_review"]:
        left.append(f"{found['needs_review']} chờ người duyệt")
    if found["not_extracted"]:
        left.append(f"{found['not_extracted']} chưa trích được")
    return text + (f"; không tính {', '.join(left)}." if left else ".")


def pick(targets: list[ed.Target], name: str | None) -> ed.Target | None:
    if not name:
        return targets[0] if len(targets) == 1 else None
    wanted = norm(name)
    for t in targets:
        if wanted in {norm(t.collection_code), norm(t.collection_name), norm(t.title)}:
            return t
    found = [t for t in targets if wanted in norm(t.collection_name) or wanted in norm(t.title)]
    return found[0] if len(found) == 1 else None


def _spans_cited(spans: Any, fields: set[str]) -> tuple[int | None, str | None]:
    """The page and quote to cite a row by: the span of a field the count reads, else the first."""
    if isinstance(spans, str):
        try:
            spans = json.loads(spans)
        except ValueError:
            spans = []
    items = [s for s in spans if isinstance(s, dict)] if isinstance(spans, list) else []
    best = next((s for s in items if s.get("field") in fields), items[0] if items else None)
    if best is None:
        return None, None
    return best.get("page_from"), best.get("quote")


class CountDocuments(Tool):
    name = "count_documents"
    title = "Đếm trên dữ liệu trích từ tài liệu"
    description = (
        "đếm hoặc cộng/trung bình/lớn nhất/nhỏ nhất trên TOÀN BỘ tài liệu của một bộ sưu tập "
        "(mỗi tài liệu một dòng, giá trị đã trích và kiểm), có điều kiện theo trường; dùng cho câu "
        "'bao nhiêu hợp đồng…', 'tổng giá trị các hoá đơn…' thay vì đọc vài đoạn tài liệu; kết "
        "quả kèm độ phủ và trích dẫn tài liệu"
    )
    Args = CountDocumentsArgs

    async def run(self, ctx: ToolContext, args: CountDocumentsArgs) -> ToolResult:  # type: ignore[override]
        from services.worker_agent import questions

        day = ctx.as_of or ctx.today
        async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
            targets = await ed.readable(session, ctx.space_id, ctx.purpose)
            target = pick(targets, args.collection)
            if target is None:
                if not targets:
                    return ToolResult(
                        False,
                        "không có bộ sưu tập tài liệu nào đã trích thành bảng mà bạn được đọc",
                        failure="unavailable",
                    )
                names = "; ".join(f"{t.collection_name} ({t.collection_code})" for t in targets)
                return ToolResult(False, f"chọn một bộ sưu tập: {names}", failure="arguments")
            profile = await ingest_profile.load(session, ctx.space_id, target.ingest_profile)
            found = await ed.coverage(session, target, day)
        try:
            sql, labels = count_sql(target, args, day, profile)
            cite = cited_sql(target, args, day, profile)
        except Refused as exc:
            return ToolResult(False, str(exc), failure="arguments")
        source = questions._internal_workspace_source(ctx.space_id, ctx.org_id)
        outcome = await run_query(
            org_id=ctx.org_id,
            data_space_id=ctx.space_id,
            principal_id=ctx.user_id,
            caller_kind="question",
            caller_id=ctx.question_id,
            source=source,
            sql=sql,
            secrets=questions.secrets(),
        )
        detail: dict[str, Any] = {
            "collection": target.collection_name,
            "query_run_id": str(outcome.query_run_id),
            "coverage": found,
        }
        if not outcome.ok:
            return ToolResult(
                False,
                "truy vấn bảng trích lỗi" if outcome.status != "timeout" else "truy vấn quá lâu",
                detail=detail,
                failure="transient" if is_transient(outcome.status, outcome.error) else "logic",
            )
        cited = await run_query(
            org_id=ctx.org_id,
            data_space_id=ctx.space_id,
            principal_id=ctx.user_id,
            caller_kind="question",
            caller_id=ctx.question_id,
            source=source,
            sql=cite,
            secrets=questions.secrets(),
            limit=CITED,
        )
        read_fields = {c.field for c in args.where} | {args.field or ""}
        citations = []
        for document_id, doc_no, title, spans in cited.rows if cited.ok else []:
            page, quote = _spans_cited(spans, read_fields)
            index = len(ctx.ws.citations) + 1
            ctx.ws.citations.append(
                {
                    "index": index,
                    "source": title or doc_no or str(document_id),
                    "type": "document",
                    "kind": "extracted_record",
                    "snippet": (quote or title or "")[:600],
                    "doc_id": str(document_id),
                    "page": page,
                }
            )
            citations.append(f"[{index}] {title or doc_no}")
        note = coverage_text(found)
        ds = ctx.ws.add(
            title=f"{target.collection_name}: {args.measure}"
            + (f" {args.field}" if args.field else "")
            + (f" theo {args.group_by}" if args.group_by else ""),
            columns=labels,
            rows=[list(r) for r in outcome.rows],
            trust="document",
            query_run_ids=[str(outcome.query_run_id)],
            step_id=ctx.step_id,
            tool=self.name,
            truncated=bool(outcome.truncated),
            coverage=note,
        )
        detail["dataset"] = ds.id
        detail["cited"] = len(citations)
        observation = ds.preview() + "\n" + note
        if citations:
            observation += "\nTài liệu được tính (trích dẫn): " + "; ".join(citations)
        return ToolResult(True, observation, [ds.id], detail)
